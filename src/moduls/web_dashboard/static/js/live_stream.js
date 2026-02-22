(() => {
  const cfg = window.LiveStreamConfig || {};
  const els = {
    status: document.getElementById("stream-status"),
    overlay: document.getElementById("stream-overlay"),
    errors: document.getElementById("stream-errors"),
    player: document.getElementById("stream-player"),
    camera: document.getElementById("stream-camera"),
    audio: document.getElementById("stream-audio"),
    start: document.getElementById("stream-start"),
    stop: document.getElementById("stream-stop"),
    refresh: document.getElementById("stream-refresh"),
    reconnect: document.getElementById("stream-reconnect"),
    open: document.getElementById("stream-open"),
    metaFfmpeg: document.getElementById("stream-ffmpeg"),
    metaRuntime: document.getElementById("stream-runtime"),
    statState: document.getElementById("stream-stat-state"),
    statFps: document.getElementById("stream-stat-fps"),
    statResolution: document.getElementById("stream-stat-resolution"),
    statCodecs: document.getElementById("stream-stat-codecs"),
    statBitrate: document.getElementById("stream-stat-bitrate"),
    statDropped: document.getElementById("stream-stat-dropped"),
    statLatency: document.getElementById("stream-stat-latency"),
    statCamera: document.getElementById("stream-stat-camera"),
    statAudio: document.getElementById("stream-stat-audio"),
    statsAnnouncer: document.getElementById("stream-stats-announcer"),
    statsPanel: document.getElementById("stream-stats"),
    statsToggle: document.getElementById("stream-stats-toggle"),
  };

  let hlsInstance = null;
  let lastRunning = false;
  let pollInFlight = false;
  let hlsRecoveryAttempts = 0;
  let hlsRecoveryWindowStart = 0;
  let runningFalseStreak = 0;
  let forceStopPending = false;
  let statsVisible = true;
  const HLS_RECOVERY_WINDOW_MS = 20000;
  const HLS_MAX_RECOVERY_ATTEMPTS = 3;
  const STATUS_FALSE_GRACE_POLLS = 3;
  const STATS_VISIBILITY_KEY = "live_stream.stats.visible";

  const telemetry = {
    stateLabel: "Остановлено",
    renderFps: Number.NaN,
    decodedFps: Number.NaN,
    resolution: "",
    codecs: "",
    bitrateKbps: Number.NaN,
    droppedPercent: Number.NaN,
    latencySec: Number.NaN,
    camera: "—",
    audio: "—",
  };

  const monitor = {
    vfcId: null,
    vfcWindowStart: 0,
    vfcFrameCount: 0,
    qualityTimerId: null,
    qualityPrev: null,
    bitrateSamples: [],
    announceLastAt: 0,
    announceLastText: "",
  };

  const setText = (el, text) => {
    if (el) el.textContent = text;
  };

  const showError = (message) => {
    if (!els.errors) return;
    if (!message) {
      els.errors.hidden = true;
      els.errors.textContent = "";
      return;
    }
    els.errors.hidden = false;
    els.errors.textContent = message;
  };

  const setOverlay = (visible) => {
    if (els.overlay) {
      els.overlay.style.display = visible ? "flex" : "none";
    }
  };

  const setStatus = (running) => {
    if (!els.status) return;
    els.status.textContent = running ? "В эфире" : "Остановлено";
    els.status.classList.toggle("badge-success", running);
    els.status.classList.toggle("badge-muted", !running);
  };

  const readStatsVisibility = () => {
    try {
      const raw = window.localStorage.getItem(STATS_VISIBILITY_KEY);
      if (raw === "0" || raw === "false") return false;
      if (raw === "1" || raw === "true") return true;
    } catch (_) {}
    return true;
  };

  const applyStatsVisibility = (visible) => {
    statsVisible = !!visible;
    if (els.statsPanel) {
      els.statsPanel.hidden = !statsVisible;
    }
    if (els.statsToggle) {
      els.statsToggle.checked = statsVisible;
      els.statsToggle.setAttribute("aria-expanded", statsVisible ? "true" : "false");
    }
    if (!statsVisible && els.statsAnnouncer) {
      els.statsAnnouncer.textContent = "";
    }
  };

  const initStatsVisibility = () => {
    applyStatsVisibility(readStatsVisibility());
    if (!els.statsToggle) return;
    els.statsToggle.addEventListener("change", () => {
      const visible = !!els.statsToggle.checked;
      applyStatsVisibility(visible);
      try {
        window.localStorage.setItem(STATS_VISIBILITY_KEY, visible ? "1" : "0");
      } catch (_) {}
    });
  };

  const asFinite = (value) => {
    const num = Number(value);
    return Number.isFinite(num) ? num : Number.NaN;
  };

  const asFiniteOrZero = (value) => {
    const num = Number(value);
    return Number.isFinite(num) ? num : 0;
  };

  const formatFixed = (value, digits = 1) => {
    if (!Number.isFinite(value)) return "—";
    return value.toFixed(digits);
  };

  const setStat = (el, value) => {
    if (!el) return;
    el.textContent = value || "—";
  };

  const renderTelemetry = () => {
    const fpsPrimary = Number.isFinite(telemetry.renderFps) ? telemetry.renderFps : telemetry.decodedFps;
    let fpsText = Number.isFinite(fpsPrimary) ? `${formatFixed(fpsPrimary, 1)} fps` : "—";
    if (
      Number.isFinite(telemetry.renderFps)
      && Number.isFinite(telemetry.decodedFps)
      && Math.abs(telemetry.renderFps - telemetry.decodedFps) >= 0.3
    ) {
      fpsText = `${formatFixed(telemetry.renderFps, 1)} fps (декодер ${formatFixed(telemetry.decodedFps, 1)})`;
    }
    const bitrateText = Number.isFinite(telemetry.bitrateKbps)
      ? `${Math.round(telemetry.bitrateKbps)} кбит/с`
      : "—";
    const droppedText = Number.isFinite(telemetry.droppedPercent)
      ? `${formatFixed(telemetry.droppedPercent, 1)}%`
      : "—";
    const latencyText = Number.isFinite(telemetry.latencySec)
      ? `${formatFixed(telemetry.latencySec, 2)} c`
      : "—";

    setStat(els.statState, telemetry.stateLabel || "—");
    setStat(els.statFps, fpsText);
    setStat(els.statResolution, telemetry.resolution || "—");
    setStat(els.statCodecs, telemetry.codecs || "—");
    setStat(els.statBitrate, bitrateText);
    setStat(els.statDropped, droppedText);
    setStat(els.statLatency, latencyText);
    setStat(els.statCamera, telemetry.camera || "—");
    setStat(els.statAudio, telemetry.audio || "—");
  };

  const announceTelemetry = (force = false) => {
    if (!els.statsAnnouncer || !statsVisible) return;
    const now = Date.now();
    if (!force && (now - monitor.announceLastAt) < 8000) return;
    const fpsPrimary = Number.isFinite(telemetry.renderFps) ? telemetry.renderFps : telemetry.decodedFps;
    const summary = [
      `Состояние: ${telemetry.stateLabel || "неизвестно"}.`,
      `FPS: ${Number.isFinite(fpsPrimary) ? formatFixed(fpsPrimary, 1) : "нет данных"}.`,
      `Разрешение: ${telemetry.resolution || "нет данных"}.`,
      `Битрейт: ${Number.isFinite(telemetry.bitrateKbps) ? `${Math.round(telemetry.bitrateKbps)} кбит/с` : "нет данных"}.`,
      `Потерянные кадры: ${Number.isFinite(telemetry.droppedPercent) ? `${formatFixed(telemetry.droppedPercent, 1)}%` : "нет данных"}.`,
      `Задержка: ${Number.isFinite(telemetry.latencySec) ? `${formatFixed(telemetry.latencySec, 2)} секунды` : "нет данных"}.`,
    ].join(" ");
    if (!force && summary === monitor.announceLastText) return;
    monitor.announceLastAt = now;
    monitor.announceLastText = summary;
    els.statsAnnouncer.textContent = summary;
  };

  const resetDynamicTelemetry = () => {
    telemetry.renderFps = Number.NaN;
    telemetry.decodedFps = Number.NaN;
    telemetry.resolution = "";
    telemetry.codecs = "";
    telemetry.bitrateKbps = Number.NaN;
    telemetry.droppedPercent = Number.NaN;
    telemetry.latencySec = Number.NaN;
  };

  const resetAllTelemetry = (stateLabel) => {
    telemetry.stateLabel = stateLabel || "Остановлено";
    telemetry.camera = "—";
    telemetry.audio = "—";
    resetDynamicTelemetry();
    monitor.bitrateSamples = [];
    monitor.qualityPrev = null;
    monitor.vfcWindowStart = 0;
    monitor.vfcFrameCount = 0;
    renderTelemetry();
  };

  const updateResolutionFromVideo = () => {
    if (!els.player) return;
    const width = asFinite(els.player.videoWidth);
    const height = asFinite(els.player.videoHeight);
    if (width > 0 && height > 0) {
      telemetry.resolution = `${Math.round(width)}x${Math.round(height)}`;
    }
  };

  const updateLatencyMetric = () => {
    if (!hlsInstance) {
      telemetry.latencySec = Number.NaN;
      return;
    }
    const latency = asFinite(hlsInstance.latency);
    telemetry.latencySec = latency > 0 ? latency : Number.NaN;
  };

  const readPlaybackQuality = () => {
    if (!els.player) return null;
    if (typeof els.player.getVideoPlaybackQuality === "function") {
      const quality = els.player.getVideoPlaybackQuality();
      if (quality && Number.isFinite(quality.totalVideoFrames)) {
        return {
          total: asFinite(quality.totalVideoFrames),
          dropped: asFiniteOrZero(quality.droppedVideoFrames),
          ts: performance.now(),
        };
      }
    }
    const total = asFinite(els.player.webkitDecodedFrameCount);
    if (Number.isFinite(total)) {
      return {
        total,
        dropped: Math.max(0, asFiniteOrZero(els.player.webkitDroppedFrameCount)),
        ts: performance.now(),
      };
    }
    return null;
  };

  const sampleQuality = () => {
    updateResolutionFromVideo();
    updateLatencyMetric();
    const current = readPlaybackQuality();
    if (!current) {
      renderTelemetry();
      announceTelemetry();
      return;
    }

    if (monitor.qualityPrev) {
      const deltaFrames = current.total - monitor.qualityPrev.total;
      const deltaDropped = current.dropped - monitor.qualityPrev.dropped;
      const deltaSec = (current.ts - monitor.qualityPrev.ts) / 1000;
      if (deltaSec > 0.2 && deltaFrames >= 0) {
        telemetry.decodedFps = deltaFrames / deltaSec;
      }
      if (deltaFrames > 0) {
        telemetry.droppedPercent = Math.max(0, (Math.max(0, deltaDropped) / deltaFrames) * 100);
      }
    }
    monitor.qualityPrev = current;
    renderTelemetry();
    announceTelemetry();
  };

  const stopQualityMonitor = () => {
    if (monitor.qualityTimerId) {
      clearInterval(monitor.qualityTimerId);
      monitor.qualityTimerId = null;
    }
    monitor.qualityPrev = null;
  };

  const startQualityMonitor = () => {
    stopQualityMonitor();
    sampleQuality();
    monitor.qualityTimerId = setInterval(sampleQuality, 1000);
  };

  const stopFrameMonitor = () => {
    if (monitor.vfcId != null && els.player && typeof els.player.cancelVideoFrameCallback === "function") {
      try { els.player.cancelVideoFrameCallback(monitor.vfcId); } catch (_) {}
    }
    monitor.vfcId = null;
    monitor.vfcWindowStart = 0;
    monitor.vfcFrameCount = 0;
  };

  const startFrameMonitor = () => {
    stopFrameMonitor();
    if (!els.player || typeof els.player.requestVideoFrameCallback !== "function") return;
    const onFrame = (now, metadata) => {
      if (!lastRunning) {
        stopFrameMonitor();
        return;
      }
      if (metadata && Number.isFinite(metadata.width) && Number.isFinite(metadata.height)) {
        telemetry.resolution = `${Math.round(metadata.width)}x${Math.round(metadata.height)}`;
      }
      if (!monitor.vfcWindowStart) {
        monitor.vfcWindowStart = now;
        monitor.vfcFrameCount = 0;
      }
      monitor.vfcFrameCount += 1;
      const delta = now - monitor.vfcWindowStart;
      if (delta >= 1000) {
        telemetry.renderFps = (monitor.vfcFrameCount * 1000) / delta;
        monitor.vfcWindowStart = now;
        monitor.vfcFrameCount = 0;
        renderTelemetry();
        announceTelemetry();
      }
      monitor.vfcId = els.player.requestVideoFrameCallback(onFrame);
    };
    monitor.vfcId = els.player.requestVideoFrameCallback(onFrame);
  };

  const stopPlaybackMonitors = () => {
    stopFrameMonitor();
    stopQualityMonitor();
  };

  const startPlaybackMonitors = () => {
    startFrameMonitor();
    startQualityMonitor();
  };

  const extractLoadDurationMs = (stats) => {
    if (!stats) return Number.NaN;
    const candidates = [
      [asFinite(stats.loading && stats.loading.start), asFinite(stats.loading && stats.loading.end)],
      [asFinite(stats.trequest), asFinite(stats.tload)],
      [asFinite(stats.loading && stats.loading.first), asFinite(stats.loading && stats.loading.end)],
    ];
    for (const [start, end] of candidates) {
      if (Number.isFinite(start) && Number.isFinite(end) && end > start) {
        return end - start;
      }
    }
    return Number.NaN;
  };

  const pushBitrateSample = (bytes, ms) => {
    if (!Number.isFinite(bytes) || !Number.isFinite(ms) || bytes <= 0 || ms <= 0) return;
    const now = Date.now();
    monitor.bitrateSamples.push({ bytes, ms, at: now });
    const cutoff = now - 30000;
    monitor.bitrateSamples = monitor.bitrateSamples.filter((s) => s.at >= cutoff);
    if (monitor.bitrateSamples.length > 24) {
      monitor.bitrateSamples = monitor.bitrateSamples.slice(-24);
    }
    const sum = monitor.bitrateSamples.reduce(
      (acc, s) => {
        acc.bytes += s.bytes;
        acc.ms += s.ms;
        return acc;
      },
      { bytes: 0, ms: 0 },
    );
    if (sum.ms > 0) {
      telemetry.bitrateKbps = ((sum.bytes * 8) / (sum.ms / 1000)) / 1000;
    }
  };

  const updateLevelInfo = (levels, index) => {
    if (!Array.isArray(levels) || !levels.length) return;
    const idx = Number.isInteger(index) && index >= 0 && index < levels.length ? index : 0;
    const level = levels[idx] || levels[0];
    if (!level) return;
    const width = asFinite(level.width);
    const height = asFinite(level.height);
    if (width > 0 && height > 0) {
      telemetry.resolution = `${Math.round(width)}x${Math.round(height)}`;
    }
    const codecs = (
      (level.attrs && level.attrs.CODECS)
      || [level.videoCodec, level.audioCodec].filter(Boolean).join(", ")
    );
    if (codecs) {
      telemetry.codecs = codecs;
    }
  };

  const fetchJson = async (url, options = {}) => {
    const resp = await fetch(url, {
      credentials: "same-origin",
      headers: {
        "Content-Type": "application/json",
        ...(cfg.csrfToken ? { "X-CSRFToken": cfg.csrfToken } : {}),
      },
      ...options,
    });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok || data.ok === false) {
      throw new Error(data.error || data.message || "Ошибка запроса.");
    }
    return data;
  };

  const destroyHls = () => {
    stopPlaybackMonitors();
    if (hlsInstance) {
      try { hlsInstance.destroy(); } catch (_) {}
      hlsInstance = null;
    }
    hlsRecoveryAttempts = 0;
    hlsRecoveryWindowStart = 0;
  };

  const attachHls = async () => {
    if (!els.player) return;
    const m3u8 = cfg.hlsUrl;
    if (!m3u8) return;
    setOverlay(false);
    destroyHls();

    els.player.pause();
    els.player.removeAttribute("src");
    els.player.load();
    monitor.bitrateSamples = [];
    telemetry.bitrateKbps = Number.NaN;
    telemetry.latencySec = Number.NaN;

    const m3u8NoCache = `${m3u8}${m3u8.includes("?") ? "&" : "?"}_ts=${Date.now()}`;
    if (els.player.canPlayType("application/vnd.apple.mpegurl")) {
      els.player.src = m3u8NoCache;
      try { await els.player.play(); } catch (_) {}
      startPlaybackMonitors();
      renderTelemetry();
      announceTelemetry(true);
      return;
    }

    if (!window.Hls) {
      try {
        await loadScript("https://cdn.jsdelivr.net/npm/hls.js@1/dist/hls.min.js");
      } catch (_) {
        showError("Не удалось загрузить hls.js. Откройте поток через VLC.");
        return;
      }
    }

    if (!window.Hls || !window.Hls.isSupported()) {
      showError("Браузер не поддерживает HLS. Откройте поток во внешнем плеере.");
      return;
    }

    hlsInstance = new window.Hls({
      enableWorker: true,
      lowLatencyMode: true,
      liveSyncDurationCount: 2,
      liveMaxLatencyDurationCount: 5,
      maxBufferLength: 6,
      maxMaxBufferLength: 12,
      backBufferLength: 4,
      manifestLoadingMaxRetry: 4,
      levelLoadingMaxRetry: 4,
      fragLoadingMaxRetry: 4,
    });
    hlsInstance.attachMedia(els.player);
    hlsInstance.on(window.Hls.Events.MEDIA_ATTACHED, () => {
      hlsInstance.loadSource(m3u8NoCache);
    });
    hlsInstance.on(window.Hls.Events.MANIFEST_PARSED, (_, data) => {
      hlsRecoveryAttempts = 0;
      hlsRecoveryWindowStart = 0;
      updateLevelInfo(data && data.levels, data && data.firstLevel);
      startPlaybackMonitors();
      renderTelemetry();
      announceTelemetry(true);
      try { els.player.play(); } catch (_) {}
    });
    hlsInstance.on(window.Hls.Events.LEVEL_SWITCHED, (_, data) => {
      const idx = data ? asFinite(data.level) : Number.NaN;
      updateLevelInfo(hlsInstance.levels, Number.isFinite(idx) ? Math.round(idx) : 0);
      renderTelemetry();
    });
    hlsInstance.on(window.Hls.Events.FRAG_LOADED, (_, data) => {
      const stats = data && data.stats ? data.stats : null;
      const loaded = asFinite(stats && (stats.loaded || stats.total));
      const loadMs = extractLoadDurationMs(stats);
      if (Number.isFinite(loaded) && Number.isFinite(loadMs)) {
        pushBitrateSample(loaded, loadMs);
        renderTelemetry();
      }
    });
    hlsInstance.on(window.Hls.Events.ERROR, (_, data) => {
      if (!data || !data.fatal) return;
      const now = Date.now();
      if (!hlsRecoveryWindowStart || (now - hlsRecoveryWindowStart) > HLS_RECOVERY_WINDOW_MS) {
        hlsRecoveryWindowStart = now;
        hlsRecoveryAttempts = 0;
      }
      const current = hlsInstance;
      if (current && hlsRecoveryAttempts < HLS_MAX_RECOVERY_ATTEMPTS) {
        hlsRecoveryAttempts += 1;
        if (data.type === window.Hls.ErrorTypes.NETWORK_ERROR) {
          try { current.startLoad(); } catch (_) {}
          return;
        }
        if (data.type === window.Hls.ErrorTypes.MEDIA_ERROR) {
          try { current.recoverMediaError(); } catch (_) {}
          return;
        }
      }
      showError("Ошибка воспроизведения HLS. Попробуйте переподключить.");
      destroyHls();
      if (lastRunning) {
        setTimeout(() => {
          if (lastRunning && !hlsInstance) {
            attachHls().catch(() => {});
          }
        }, 600);
      }
    });
  };

  const loadScript = (src) => new Promise((resolve, reject) => {
    const s = document.createElement("script");
    s.src = src;
    s.onload = resolve;
    s.onerror = reject;
    document.head.appendChild(s);
  });

  const updateDevices = async () => {
    try {
      const resp = await fetchJson(cfg.devicesUrl);
      const data = resp.data || {};
      const videos = data.videos || [];
      const audios = data.audios || [];

      els.camera.innerHTML = "";
      videos.forEach((dev) => {
        const opt = document.createElement("option");
        const source = dev.source || "dshow";
        const valueName = dev.name;
        opt.value = `${source}::${valueName}`;
        if (dev.alt_name) {
          opt.dataset.alt = dev.alt_name;
        }
        opt.textContent = dev.label || dev.name;
        els.camera.appendChild(opt);
      });

      els.audio.innerHTML = "";
      const noneOpt = document.createElement("option");
      noneOpt.value = "";
      noneOpt.textContent = "Без звука";
      els.audio.appendChild(noneOpt);
      audios.forEach((dev) => {
        const opt = document.createElement("option");
        opt.value = dev.alt_name || dev.name;
        opt.textContent = dev.label || dev.name;
        els.audio.appendChild(opt);
      });

      if (!videos.length) {
        showError("Камеры не найдены.");
      }

      const ffmpegText = data.ffmpeg_found
        ? `Найден (${data.ffmpeg_path || "ffmpeg"})`
        : "Не найден";
      setText(els.metaFfmpeg, ffmpegText);
    } catch (err) {
      showError(err.message || "Не удалось получить устройства.");
    }
  };

  const updateFromStatusPayload = (data, running) => {
    telemetry.stateLabel = running ? "В эфире" : "Остановлено";
    const camera = data && data.camera ? (data.camera.label || data.camera.name || "") : "";
    const audio = data && data.audio ? (data.audio.label || data.audio.name || "") : "";
    if (camera) {
      telemetry.camera = camera;
    } else if (!running) {
      telemetry.camera = "—";
    }
    if (audio) {
      telemetry.audio = audio;
    } else if (!running) {
      telemetry.audio = "—";
    }
    if (!running) {
      resetDynamicTelemetry();
    }
    renderTelemetry();
  };

  const pollStatus = async () => {
    if (pollInFlight) return;
    pollInFlight = true;
    try {
      const resp = await fetchJson(cfg.statusUrl);
      const data = resp.data || {};
      const reportedRunning = !!data.running;
      if (reportedRunning) {
        runningFalseStreak = 0;
        forceStopPending = false;
      } else {
        runningFalseStreak += 1;
      }
      const running = reportedRunning || (
        lastRunning
        && !forceStopPending
        && runningFalseStreak < STATUS_FALSE_GRACE_POLLS
      );
      setStatus(running);
      setOverlay(!running);
      if (data.last_error && (reportedRunning || !running)) {
        showError(data.last_error);
      } else if (!running) {
        showError("");
      }

      updateFromStatusPayload(data, running);

      const stateChanged = running !== lastRunning;
      if (running) {
        if (!lastRunning || !hlsInstance) {
          await attachHls();
        }
      } else if (lastRunning) {
        destroyHls();
        runningFalseStreak = 0;
      }
      lastRunning = running;

      const runtimeText = running ? "Работает" : "Остановлено";
      setText(els.metaRuntime, runtimeText);
      announceTelemetry(stateChanged);
    } catch (err) {
      showError(err.message || "Ошибка статуса.");
    } finally {
      pollInFlight = false;
    }
  };

  const startStream = async () => {
    if (!cfg.canWrite) return;
    showError("");
    try {
      forceStopPending = false;
      runningFalseStreak = 0;
      const video = els.camera.value || "";
      const selected = els.camera.selectedOptions && els.camera.selectedOptions[0];
      const videoAlt = selected && selected.dataset ? selected.dataset.alt || "" : "";
      const audio = els.audio.value || "";
      await fetchJson(cfg.startUrl, {
        method: "POST",
        body: JSON.stringify({ video, video_alt: videoAlt, audio }),
      });
      await pollStatus();
      setTimeout(() => { pollStatus(); }, 1200);
      setTimeout(() => { pollStatus(); }, 2500);
    } catch (err) {
      showError(err.message || "Не удалось запустить.");
    }
  };

  const stopStream = async () => {
    if (!cfg.canWrite) return;
    showError("");
    try {
      await fetchJson(cfg.stopUrl, {
        method: "POST",
        body: JSON.stringify({}),
      });
      forceStopPending = true;
      runningFalseStreak = STATUS_FALSE_GRACE_POLLS;
      destroyHls();
      lastRunning = false;
      resetAllTelemetry("Остановлено");
      await pollStatus();
    } catch (err) {
      showError(err.message || "Не удалось остановить.");
    }
  };

  if (els.player) {
    els.player.addEventListener("loadedmetadata", () => {
      updateResolutionFromVideo();
      renderTelemetry();
    });
  }

  if (els.start) els.start.addEventListener("click", startStream);
  if (els.stop) els.stop.addEventListener("click", stopStream);
  if (els.refresh) els.refresh.addEventListener("click", updateDevices);
  if (els.reconnect) els.reconnect.addEventListener("click", attachHls);
  if (els.open) {
    els.open.addEventListener("click", () => {
      if (cfg.hlsUrl) window.open(cfg.hlsUrl, "_blank");
    });
  }

  initStatsVisibility();

  if (!cfg.canWrite) {
    if (els.start) els.start.disabled = true;
    if (els.stop) els.stop.disabled = true;
  }

  resetAllTelemetry("Остановлено");
  updateDevices().then(pollStatus);
  setInterval(pollStatus, 2000);
})();
