(() => {
  const init = async () => {
    const messages = {
      configLoading: "Загружаю конфигурацию TTS...",
      configFail:
        "Не удалось загрузить конфигурацию TTS. Обновите страницу или проверьте логи.",
      csrfMissing:
        "Не удалось получить CSRF-токен. Обновите страницу и попробуйте снова.",
      textEmpty: "Введите текст для синтеза.",
      synthRunning: "Выполняю синтез речи...",
      synthDone: "Синтез завершен.",
      synthFail: "Синтез завершился ошибкой.",
      sendFail: "Не удалось отправить запрос на сервер.",
      installRunning:
        "Устанавливаю RHVoice-addon. Это может занять несколько минут...",
      installDone: "RHVoice-addon установлен.",
      installFail: "Не удалось установить RHVoice-addon автоматически.",
      installLogIdle: "Установка еще не запускалась.",
      installLogStarted: "Запущена установка RHVoice-addon.",
      installLogDone: "Установка RHVoice-addon завершена успешно.",
      installLogError: "Установка RHVoice-addon завершилась ошибкой.",
      diagnosticsEmpty: "Диагностика зависимостей недоступна.",
      historyEmpty: "История пока пустая.",
      historyUnavailable:
        "История недоступна в этом браузере (нет доступа к localStorage).",
      historyCleared: "История очищена.",
      liveLogIdle: "Лог появится после запуска синтеза.",
      liveLogEmpty: "Синтез еще не запускался.",
      liveLogQueued: "Запрос принят. Ожидаю запуск фоновой задачи.",
      liveLogNetworkError: "Не удалось получить обновление лога с сервера.",
    };

    const root = document.getElementById("wintts-root");
    const engineSelect = document.getElementById("wintts-engine");
    const voiceSelect = document.getElementById("wintts-voice");
    const textArea = document.getElementById("wintts-text");
    const fileInput = document.getElementById("wintts-file");
    const importFileButton = document.getElementById("wintts-import-file");
    const clearFileButton = document.getElementById("wintts-clear-file");
    const fileInfoBox = document.getElementById("wintts-file-info");
    const fileHint = document.getElementById("wintts-file-hint");
    const runButton = document.getElementById("wintts-run");
    const clearButton = document.getElementById("wintts-clear");
    const installButton = document.getElementById("wintts-install");
    const installLogDetails = document.getElementById("wintts-install-log-details");
    const installLogStatus = document.getElementById("wintts-install-log-status");
    const installLogOutput = document.getElementById("wintts-install-log-output");
    const output = document.getElementById("wintts-output");
    const diagnosticsBox = document.getElementById("wintts-diagnostics");
    const audioSection = document.getElementById("wintts-audio-section");
    const audioPlayer = document.getElementById("wintts-audio");
    const downloadLink = document.getElementById("wintts-download");
    const historyBox = document.getElementById("wintts-history");
    const historyClearButton = document.getElementById("wintts-history-clear");
    const engineNote = document.getElementById("wintts-engine-note");
    const edgeLanguageWrap = document.getElementById("wintts-edge-language-wrap");
    const edgeLanguageSelect = document.getElementById("wintts-edge-language");
    const dualLanguageToggleWrap = document.getElementById("wintts-dual-language-toggle-wrap");
    const dualLanguageEnabled = document.getElementById("wintts-dual-language-enabled");
    const dualLanguageWrap = document.getElementById("wintts-dual-language-wrap");
    const dualLanguageEmptyHint = document.getElementById("wintts-dual-language-empty-hint");
    const secondaryLanguageSelect = document.getElementById("wintts-secondary-language");
    const secondaryVoiceSelect = document.getElementById("wintts-secondary-voice");
    const secondaryLanguageHint = document.getElementById("wintts-secondary-language-hint");
    const secondaryProsodyWrap = document.getElementById("wintts-secondary-prosody-wrap");
    const secondaryRateInput = document.getElementById("wintts-secondary-edge-rate");
    const secondaryPitchInput = document.getElementById("wintts-secondary-edge-pitch");
    const secondaryVolumeInput = document.getElementById("wintts-secondary-edge-volume");
    const secondaryProsodyHint = document.getElementById("wintts-secondary-prosody-hint");
    const dualPauseWrap = document.getElementById("wintts-dual-pause-wrap");
    const dualPauseModeSelect = document.getElementById("wintts-dual-pause-mode");
    const dualPauseMsWrap = document.getElementById("wintts-dual-pause-ms-wrap");
    const dualPauseMsInput = document.getElementById("wintts-dual-pause-ms");
    const dualPauseHint = document.getElementById("wintts-dual-pause-hint");
    const edgeParallelismWrap = document.getElementById("wintts-edge-parallelism-wrap");
    const edgeParallelismSelect = document.getElementById("wintts-edge-parallelism");
    const edgeParallelismHint = document.getElementById("wintts-edge-parallelism-hint");
    const googleRetryWrap = document.getElementById("wintts-google-retry-wrap");
    const googleRetrySelect = document.getElementById("wintts-google-retry-count");
    const googleRetryHint = document.getElementById("wintts-google-retry-hint");
    const edgeProsodyWrap = document.getElementById("wintts-edge-prosody-wrap");
    const edgeRateInput = document.getElementById("wintts-edge-rate");
    const edgePitchInput = document.getElementById("wintts-edge-pitch");
    const edgeVolumeInput = document.getElementById("wintts-edge-volume");
    const edgeProsodyHint = document.getElementById("wintts-edge-prosody-hint");
    const edgeNormalizerWrap = document.getElementById("wintts-edge-normalizer-wrap");
    const edgeNormalizerEnabled = document.getElementById("wintts-edge-normalizer-enabled");
    const edgeNormalizerPreset = document.getElementById("wintts-edge-normalizer-preset");
    const edgeNormalizerAuto = document.getElementById("wintts-edge-normalizer-auto");
    const edgeNormalizerStripMarkdown = document.getElementById("wintts-edge-normalizer-strip-markdown");
    const edgeNormalizerUnwrapMarkdownLinks = document.getElementById("wintts-edge-normalizer-unwrap-markdown-links");
    const edgeNormalizerStripUrls = document.getElementById("wintts-edge-normalizer-strip-urls");
    const edgeNormalizerStripEmails = document.getElementById("wintts-edge-normalizer-strip-emails");
    const edgeNormalizerCollapseSymbols = document.getElementById("wintts-edge-normalizer-collapse-symbols");
    const edgeNormalizerCollapsePunctuation = document.getElementById("wintts-edge-normalizer-collapse-punctuation");
    const edgeNormalizerPreserveEllipsis = document.getElementById("wintts-edge-normalizer-preserve-ellipsis");
    const edgeNormalizerDropSymbolTokens = document.getElementById("wintts-edge-normalizer-drop-symbol-tokens");
    const edgeNormalizerWhitespace = document.getElementById("wintts-edge-normalizer-normalize-whitespace");
    const edgeNormalizerCustomSymbolsInput = document.getElementById("wintts-edge-normalizer-custom-symbols");
    const edgeNormalizerHint = document.getElementById("wintts-edge-normalizer-hint");
    const edgeNormalizerSummary = document.getElementById("wintts-edge-normalizer-summary");
    const edgeNormalizerAnalyzerWrap = document.getElementById("wintts-edge-normalizer-analyzer");
    const edgeNormalizerAnalyzerAuto = document.getElementById("wintts-edge-normalizer-analyzer-auto");
    const edgeNormalizerAnalyzeButton = document.getElementById("wintts-edge-normalizer-analyze");
    const edgeNormalizerAnalyzerStatus = document.getElementById("wintts-edge-normalizer-analyzer-status");
    const edgeNormalizerPreviewBefore = document.getElementById("wintts-edge-normalizer-preview-before");
    const edgeNormalizerPreviewAfter = document.getElementById("wintts-edge-normalizer-preview-after");
    const edgeNormalizerSymbols = document.getElementById("wintts-edge-normalizer-symbols");
    const edgeNormalizerReport = document.getElementById("wintts-edge-normalizer-report");
    const edgeNormalizerRecommendations = document.getElementById("wintts-edge-normalizer-recommendations");
    const liveLogDetails = document.getElementById("wintts-live-log");
    const liveLogStatus = document.getElementById("wintts-live-log-status");
    const liveLogPercent = document.getElementById("wintts-live-log-percent");
    const liveLogList = document.getElementById("wintts-live-log-list");
    const liveLogEmpty = document.getElementById("wintts-live-log-empty");
    const liveLogProgressBar = document.getElementById("wintts-live-log-progress-bar");
    const edgeLanguageLabel = edgeLanguageWrap ? edgeLanguageWrap.querySelector("label") : null;
    const edgeParallelismLabel = edgeParallelismWrap ? edgeParallelismWrap.querySelector("label") : null;
    const googleRetryLabel = googleRetryWrap ? googleRetryWrap.querySelector("label") : null;
    const edgeProsodyTitle = edgeProsodyWrap ? edgeProsodyWrap.querySelector("label") : null;

    if (
      !root ||
      !engineSelect ||
      !voiceSelect ||
      !textArea ||
      !runButton ||
      !output ||
      !diagnosticsBox ||
      !audioSection ||
      !audioPlayer ||
      !downloadLink ||
      !historyBox
    ) {
      return;
    }

    const config = {
      configUrl: root.dataset.configUrl || "",
      synthUrl: root.dataset.synthUrl || "",
      synthStartUrl: root.dataset.synthStartUrl || "",
      synthStatusUrlTemplate: root.dataset.synthStatusUrlTemplate || "",
      importUrl: root.dataset.importUrl || "",
      normalizePreviewUrl: root.dataset.normalizePreviewUrl || "",
      installUrl: root.dataset.installUrl || "",
      csrfToken: root.dataset.csrfToken || "",
    };

    const historyKey = "wintts.history.v10";
    const historyLimit = 20;
    let historyAvailable = true;
    let busy = false;
    let voicesByEngine = {};
    let maxTextLen = 5000;
    let uiMaxTextLen = 5000;
    let maxTextLenByEngine = {};
    let maxUploadBytes = 0;
    let currentSourceMeta = {
      sourceName: "",
      sourceExt: "",
      title: "",
      charCount: 0,
      sizeBytes: 0,
    };
    let activeSynthesisJobId = "";
    let activeSynthesisRequest = null;
    let liveLogPollTimer = 0;
    let edgeParallelismConfig = {
      min: 1,
      max: 12,
      default: 4,
      current: 4,
    };
    let googleParallelismConfig = {
      min: 1,
      max: 12,
      default: 4,
      current: 4,
    };
    let googleRetryConfig = {
      min: 0,
      max: 8,
      default: 2,
      current: 2,
    };
    let edgeOptionsConfig = {
      rate: { enabled: true, min: -100, max: 100, default: 0, unit: "%" },
      pitch: { enabled: true, min: -100, max: 100, default: 0, unit: "Hz" },
      volume: { enabled: true, min: -100, max: 100, default: 0, unit: "%" },
    };
    let dualPauseConfig = {
      available: true,
      default_mode: "auto",
      modes: [
        { id: "auto", label: "Авто" },
        { id: "manual", label: "Ручная" },
        { id: "off", label: "Без нормализации" },
      ],
      ms: { min: 0, max: 1500, default: 90 },
      auto: {
        hint: "Авто-режим уменьшает избыточные паузы между фрагментами разных языков.",
      },
    };
    const edgeNormalizerDefaultSettings = {
      enabled: true,
      preset: "balanced",
      auto_tune: true,
      strip_markdown: true,
      unwrap_markdown_links: true,
      strip_urls: true,
      strip_emails: true,
      collapse_repeated_symbols: true,
      collapse_repeated_punctuation: true,
      preserve_ellipsis: true,
      drop_symbol_only_tokens: true,
      normalize_whitespace: true,
      drop_symbols: [],
    };
    const edgeNormalizerAnalyzableSymbols = "*#@_^~=+/\\|<>[]{}";
    const edgeNormalizerSettingKeys = [
      "enabled",
      "preset",
      "auto_tune",
      "strip_markdown",
      "unwrap_markdown_links",
      "strip_urls",
      "strip_emails",
      "collapse_repeated_symbols",
      "collapse_repeated_punctuation",
      "preserve_ellipsis",
      "drop_symbol_only_tokens",
      "normalize_whitespace",
      "drop_symbols",
    ];
    let edgeNormalizerConfig = {
      available: true,
      default: { ...edgeNormalizerDefaultSettings },
      presets: [],
      auto_tune: {
        enabled: true,
        balanced_threshold: 12000,
        aggressive_threshold: 60000,
        hint: "",
      },
    };
    let edgeNormalizerLastPreset = "balanced";
    let edgeNormalizerSelectedDropSymbols = [];
    let edgeNormalizerLastSymbolDelta = [];
    let edgeNormalizerAnalyzeTimer = 0;
    let edgeNormalizerAnalyzeRequestId = 0;
    let engineOptionsByEngine = {};
    let voiceCatalogByEngine = {};
    let voiceToLanguageByEngine = {};
    let languageDisplayNames = null;
    try {
      languageDisplayNames = new Intl.DisplayNames(["ru"], { type: "language" });
    } catch (_err) {
      languageDisplayNames = null;
    }

    const setOutput = (message, ok) => {
      const isError = ok === false;
      output.textContent = message;
      output.classList.toggle("is-ok", Boolean(ok));
      output.classList.toggle("is-error", isError);
      output.setAttribute("aria-live", isError ? "assertive" : "polite");
    };

    const parseJsonResponse = async (response) => {
      const rawText = await response.text();
      if (!rawText) {
        return { data: null, rawText: "", parseError: "пустой ответ" };
      }
      try {
        return { data: JSON.parse(rawText), rawText, parseError: "" };
      } catch (err) {
        return {
          data: null,
          rawText,
          parseError: err && err.message ? String(err.message) : "невалидный JSON",
        };
      }
    };

    const toBodySnippet = (text, maxLen = 600) => {
      const normalized = String(text || "").replace(/\r/g, "").trim();
      if (!normalized) {
        return "";
      }
      if (normalized.length <= maxLen) {
        return normalized;
      }
      return `${normalized.slice(0, maxLen - 3)}...`;
    };

    const toLines = (value) => {
      if (Array.isArray(value)) {
        return value
          .map((line) => String(line || "").trim())
          .filter(Boolean);
      }
      if (typeof value === "string") {
        return value
          .split(/\r?\n/)
          .map((line) => line.trim())
          .filter(Boolean);
      }
      return [];
    };

    const toBool = (value, fallback = false) => {
      if (typeof value === "boolean") {
        return value;
      }
      if (value === null || value === undefined) {
        return fallback;
      }
      const text = String(value).trim().toLowerCase();
      if (!text) {
        return fallback;
      }
      if (["1", "true", "yes", "on", "да"].includes(text)) {
        return true;
      }
      if (["0", "false", "no", "off", "нет"].includes(text)) {
        return false;
      }
      return fallback;
    };

    let edgeNormalizerLetterOrNumberRe = null;
    try {
      edgeNormalizerLetterOrNumberRe = new RegExp("[\\p{L}\\p{N}]", "u");
    } catch (_err) {
      edgeNormalizerLetterOrNumberRe = null;
    }

    const isAllowedDropSymbol = (rawSymbol) => {
      const symbol = String(rawSymbol || "");
      if (!symbol || symbol.length !== 1) {
        return false;
      }
      if (/\s/.test(symbol)) {
        return false;
      }
      if (edgeNormalizerAnalyzableSymbols.includes(symbol)) {
        return true;
      }
      if (edgeNormalizerLetterOrNumberRe) {
        return !edgeNormalizerLetterOrNumberRe.test(symbol);
      }
      return !/[A-Za-z0-9А-Яа-яЁё]/.test(symbol);
    };

    const normalizeDropSymbols = (rawValue, fallback = []) => {
      const base = [];
      let hasExplicitSource = false;
      if (Array.isArray(fallback)) {
        fallback.forEach((item) => {
          String(item || "").split("").forEach((symbol) => {
            if (isAllowedDropSymbol(symbol) && !base.includes(symbol)) {
              base.push(symbol);
            }
          });
        });
      }
      const source = [];
      if (Array.isArray(rawValue)) {
        hasExplicitSource = true;
        source.push(...rawValue);
      } else if (typeof rawValue === "string") {
        hasExplicitSource = true;
        const trimmed = rawValue.trim();
        if (trimmed) {
          if (trimmed.startsWith("[") && trimmed.endsWith("]")) {
            try {
              const parsed = JSON.parse(trimmed);
              if (Array.isArray(parsed)) {
                source.push(...parsed);
              } else {
                source.push(trimmed);
              }
            } catch (_err) {
              source.push(trimmed);
            }
          } else if (trimmed.includes(",")) {
            trimmed.split(",").forEach((chunk) => source.push(chunk));
          } else {
            source.push(trimmed);
          }
        }
      } else {
        source.push(...base);
      }

      const normalized = [];
      source.forEach((item) => {
        String(item || "").split("").forEach((symbol) => {
          if (!isAllowedDropSymbol(symbol)) {
            return;
          }
          if (!normalized.includes(symbol)) {
            normalized.push(symbol);
          }
        });
      });
      if (!normalized.length && base.length && !hasExplicitSource) {
        return [...base];
      }
      return normalized.slice(0, 32);
    };

    const setEdgeNormalizerSelectedDropSymbols = (rawValue, syncInput = true) => {
      edgeNormalizerSelectedDropSymbols = normalizeDropSymbols(rawValue, edgeNormalizerSelectedDropSymbols);
      if (syncInput && edgeNormalizerCustomSymbolsInput) {
        edgeNormalizerCustomSymbolsInput.value = edgeNormalizerSelectedDropSymbols.join("");
      }
      return normalizeDropSymbols(edgeNormalizerSelectedDropSymbols);
    };

    const normalizeEdgeNormalizerSettings = (rawSettings = {}, fallback = edgeNormalizerDefaultSettings) => {
      const source = rawSettings && typeof rawSettings === "object" ? rawSettings : {};
      return {
        enabled: toBool(source.enabled, toBool(fallback.enabled, true)),
        preset: String(source.preset || fallback.preset || "balanced"),
        auto_tune: toBool(source.auto_tune, toBool(fallback.auto_tune, true)),
        strip_markdown: toBool(source.strip_markdown, toBool(fallback.strip_markdown, true)),
        unwrap_markdown_links: toBool(
          source.unwrap_markdown_links,
          toBool(fallback.unwrap_markdown_links, true)
        ),
        strip_urls: toBool(source.strip_urls, toBool(fallback.strip_urls, true)),
        strip_emails: toBool(source.strip_emails, toBool(fallback.strip_emails, true)),
        collapse_repeated_symbols: toBool(
          source.collapse_repeated_symbols,
          toBool(fallback.collapse_repeated_symbols, true)
        ),
        collapse_repeated_punctuation: toBool(
          source.collapse_repeated_punctuation,
          toBool(fallback.collapse_repeated_punctuation, true)
        ),
        preserve_ellipsis: toBool(
          source.preserve_ellipsis,
          toBool(fallback.preserve_ellipsis, true)
        ),
        drop_symbol_only_tokens: toBool(
          source.drop_symbol_only_tokens,
          toBool(fallback.drop_symbol_only_tokens, true)
        ),
        normalize_whitespace: toBool(source.normalize_whitespace, toBool(fallback.normalize_whitespace, true)),
        drop_symbols: normalizeDropSymbols(
          source.drop_symbols,
          Array.isArray(fallback.drop_symbols) ? fallback.drop_symbols : []
        ),
      };
    };

    const getNormalizerPresetMap = () => {
      const map = {};
      const presets = Array.isArray(edgeNormalizerConfig?.presets) ? edgeNormalizerConfig.presets : [];
      presets.forEach((item) => {
        const id = String(item?.id || "").trim();
        if (!id) {
          return;
        }
        map[id] = {
          id,
          label: String(item?.label || id),
          description: String(item?.description || ""),
        };
      });
      return map;
    };

    const getEdgeNormalizerSettings = () => {
      const presetValueRaw = String(edgeNormalizerPreset?.value || "balanced");
      const presetValue = presetValueRaw === "__custom__"
        ? String(edgeNormalizerLastPreset || edgeNormalizerConfig?.default?.preset || "balanced")
        : presetValueRaw;
      const selectedDropSymbols = getSelectedDropSymbolsFromUI();
      return normalizeEdgeNormalizerSettings(
        {
          enabled: edgeNormalizerEnabled?.checked,
          preset: presetValue,
          auto_tune: edgeNormalizerAuto?.checked,
          strip_markdown: edgeNormalizerStripMarkdown?.checked,
          unwrap_markdown_links: edgeNormalizerUnwrapMarkdownLinks?.checked,
          strip_urls: edgeNormalizerStripUrls?.checked,
          strip_emails: edgeNormalizerStripEmails?.checked,
          collapse_repeated_symbols: edgeNormalizerCollapseSymbols?.checked,
          collapse_repeated_punctuation: edgeNormalizerCollapsePunctuation?.checked,
          preserve_ellipsis: edgeNormalizerPreserveEllipsis?.checked,
          drop_symbol_only_tokens: edgeNormalizerDropSymbolTokens?.checked,
          normalize_whitespace: edgeNormalizerWhitespace?.checked,
          drop_symbols: selectedDropSymbols,
        },
        edgeNormalizerConfig?.default || edgeNormalizerDefaultSettings
      );
    };

    const setEdgeNormalizerSettings = (rawSettings = {}, preservePreset = false) => {
      const next = normalizeEdgeNormalizerSettings(rawSettings, edgeNormalizerConfig?.default || edgeNormalizerDefaultSettings);
      if (edgeNormalizerEnabled) {
        edgeNormalizerEnabled.checked = next.enabled;
      }
      if (edgeNormalizerAuto) {
        edgeNormalizerAuto.checked = next.auto_tune;
      }
      if (edgeNormalizerStripMarkdown) {
        edgeNormalizerStripMarkdown.checked = next.strip_markdown;
      }
      if (edgeNormalizerUnwrapMarkdownLinks) {
        edgeNormalizerUnwrapMarkdownLinks.checked = next.unwrap_markdown_links;
      }
      if (edgeNormalizerStripUrls) {
        edgeNormalizerStripUrls.checked = next.strip_urls;
      }
      if (edgeNormalizerStripEmails) {
        edgeNormalizerStripEmails.checked = next.strip_emails;
      }
      if (edgeNormalizerCollapseSymbols) {
        edgeNormalizerCollapseSymbols.checked = next.collapse_repeated_symbols;
      }
      if (edgeNormalizerCollapsePunctuation) {
        edgeNormalizerCollapsePunctuation.checked = next.collapse_repeated_punctuation;
      }
      if (edgeNormalizerPreserveEllipsis) {
        edgeNormalizerPreserveEllipsis.checked = next.preserve_ellipsis;
      }
      if (edgeNormalizerDropSymbolTokens) {
        edgeNormalizerDropSymbolTokens.checked = next.drop_symbol_only_tokens;
      }
      if (edgeNormalizerWhitespace) {
        edgeNormalizerWhitespace.checked = next.normalize_whitespace;
      }
      setEdgeNormalizerSelectedDropSymbols(
        next.drop_symbols,
        true
      );
      if (next.preset && next.preset !== "__custom__") {
        edgeNormalizerLastPreset = String(next.preset);
      }
      if (!preservePreset && edgeNormalizerPreset) {
        edgeNormalizerPreset.value = String(next.preset || "balanced");
      }
    };

    const updateEdgeNormalizerSummary = (customMessage = "") => {
      if (!edgeNormalizerSummary) {
        return;
      }
      if (customMessage) {
        edgeNormalizerSummary.textContent = customMessage;
        return;
      }
      const settings = getEdgeNormalizerSettings();
      if (!settings.enabled) {
        edgeNormalizerSummary.textContent = "Нормализация отключена.";
        return;
      }
      const activeFlags = [
        settings.strip_markdown ? "markdown" : "",
        settings.unwrap_markdown_links ? "распаковка markdown-ссылок" : "",
        settings.strip_urls ? "URL" : "",
        settings.strip_emails ? "email" : "",
        settings.collapse_repeated_symbols ? "повторы символов" : "",
        settings.collapse_repeated_punctuation ? "повторы пунктуации" : "",
        settings.preserve_ellipsis ? "сохранять многоточие" : "",
        settings.drop_symbol_only_tokens ? "шумовые токены" : "",
        settings.normalize_whitespace ? "пробелы/пустые строки" : "",
      ].filter(Boolean);
      const auto = settings.auto_tune ? "автонастройка вкл" : "автонастройка выкл";
      const customSymbols = Array.isArray(settings.drop_symbols) ? settings.drop_symbols : [];
      const customSymbolsLabel = customSymbols.length
        ? `Ручные символы: ${customSymbols.join("")}.`
        : "Ручные символы не заданы.";
      edgeNormalizerSummary.textContent = `Профиль: ${settings.preset}. Активно: ${activeFlags.join(", ") || "базовая очистка"}. ${auto}. ${customSymbolsLabel}`;
    };

    const renderEdgeNormalizerPresetOptions = (selectedPreset = "") => {
      if (!edgeNormalizerPreset) {
        return;
      }
      const presetMap = getNormalizerPresetMap();
      const current = String(selectedPreset || edgeNormalizerConfig?.default?.preset || "balanced");
      while (edgeNormalizerPreset.options.length) {
        edgeNormalizerPreset.remove(0);
      }
      const ids = Object.keys(presetMap);
      if (!ids.length) {
        ["soft", "balanced", "aggressive"].forEach((id) => {
          const option = document.createElement("option");
          option.value = id;
          option.textContent = id;
          edgeNormalizerPreset.appendChild(option);
        });
      } else {
        ids.forEach((id) => {
          const option = document.createElement("option");
          option.value = id;
          option.textContent = String(presetMap[id]?.label || id);
          edgeNormalizerPreset.appendChild(option);
        });
      }

      const customOption = document.createElement("option");
      customOption.value = "__custom__";
      customOption.textContent = "Пользовательский";
      edgeNormalizerPreset.appendChild(customOption);

      const target = Array.from(edgeNormalizerPreset.options).some((item) => item.value === current)
        ? current
        : String(edgeNormalizerConfig?.default?.preset || "balanced");
      edgeNormalizerPreset.value = target || "balanced";
      if (edgeNormalizerPreset.value && edgeNormalizerPreset.value !== "__custom__") {
        edgeNormalizerLastPreset = String(edgeNormalizerPreset.value);
      }
    };

    const markEdgeNormalizerPresetCustom = () => {
      if (!edgeNormalizerPreset) {
        return;
      }
      const hasCustom = Array.from(edgeNormalizerPreset.options).some((item) => item.value === "__custom__");
      if (!hasCustom) {
        return;
      }
      if (edgeNormalizerPreset.value !== "__custom__") {
        edgeNormalizerPreset.value = "__custom__";
      }
    };

    const applyEdgeNormalizerPreset = (presetId, resetSummary = true) => {
      const presetMap = getNormalizerPresetMap();
      const preset = String(presetId || "").trim();
      const fromPreset = presetMap[preset];
      if (!fromPreset) {
        if (resetSummary) {
          updateEdgeNormalizerSummary();
        }
        return;
      }

      const defaults = normalizeEdgeNormalizerSettings(edgeNormalizerConfig?.default || {}, edgeNormalizerDefaultSettings);
      const presetSettings = normalizeEdgeNormalizerSettings(
        { ...defaults, ...(edgeNormalizerConfig?.default || {}), preset },
        defaults
      );
      const profileDefaults = (edgeNormalizerConfig?.profiles && edgeNormalizerConfig.profiles[preset]) || null;
      if (profileDefaults && typeof profileDefaults === "object") {
        Object.keys(profileDefaults).forEach((key) => {
          if (edgeNormalizerSettingKeys.includes(key)) {
            if (key === "preset") {
              presetSettings[key] = String(profileDefaults[key]);
            } else if (key === "drop_symbols") {
              presetSettings[key] = normalizeDropSymbols(profileDefaults[key], presetSettings[key]);
            } else {
              presetSettings[key] = toBool(profileDefaults[key], presetSettings[key]);
            }
          }
        });
      }
      setEdgeNormalizerSettings({ ...presetSettings, preset }, true);
      if (edgeNormalizerPreset) {
        edgeNormalizerPreset.value = preset;
      }
      edgeNormalizerLastPreset = preset;
      if (edgeNormalizerHint) {
        edgeNormalizerHint.textContent = fromPreset.description || edgeNormalizerHint.textContent;
      }
      if (resetSummary) {
        updateEdgeNormalizerSummary();
      }
    };

    const applyEdgeNormalizerConfig = (payload, resetValues = true) => {
      const source = payload && typeof payload === "object" ? payload : {};
      const defaultSettings = normalizeEdgeNormalizerSettings(
        source.default || {},
        edgeNormalizerDefaultSettings
      );
      const profiles = {};
      const presets = Array.isArray(source.presets) ? source.presets : [];
      presets.forEach((item) => {
        const id = String(item?.id || "").trim();
        if (!id) {
          return;
        }
        const profileCandidate = source?.profiles?.[id];
        if (profileCandidate && typeof profileCandidate === "object") {
          profiles[id] = normalizeEdgeNormalizerSettings(
            { ...defaultSettings, ...profileCandidate, preset: id },
            defaultSettings
          );
        } else {
          profiles[id] = normalizeEdgeNormalizerSettings(
            { ...defaultSettings, preset: id },
            defaultSettings
          );
        }
      });

      edgeNormalizerConfig = {
        available: toBool(source.available, true),
        default: defaultSettings,
        presets,
        profiles,
        auto_tune: {
          enabled: toBool(source?.auto_tune?.enabled, true),
          balanced_threshold: Number(source?.auto_tune?.balanced_threshold || 12000),
          aggressive_threshold: Number(source?.auto_tune?.aggressive_threshold || 60000),
          hint: String(source?.auto_tune?.hint || ""),
        },
      };
      edgeNormalizerLastPreset = String(defaultSettings.preset || "balanced");
      setEdgeNormalizerSelectedDropSymbols(defaultSettings.drop_symbols, true);

      renderEdgeNormalizerPresetOptions(defaultSettings.preset);
      if (resetValues) {
        setEdgeNormalizerSettings(defaultSettings, true);
        applyEdgeNormalizerPreset(defaultSettings.preset, false);
      }
      if (edgeNormalizerHint && edgeNormalizerConfig.auto_tune.hint) {
        edgeNormalizerHint.textContent = edgeNormalizerConfig.auto_tune.hint;
      }
      updateEdgeNormalizerSummary();
    };

    const clampPercent = (value) => {
      const parsed = Number(value);
      if (!Number.isFinite(parsed)) {
        return 0;
      }
      return Math.max(0, Math.min(100, Math.round(parsed)));
    };

    const getLiveLogStatusLabel = (status) => {
      if (status === "done") {
        return "Готово";
      }
      if (status === "error") {
        return "Ошибка";
      }
      if (status === "running") {
        return "Выполняется";
      }
      if (status === "queued") {
        return "В очереди";
      }
      return "Ожидание";
    };

    const formatLogTime = (ts) => {
      const stamp = Number(ts || 0);
      if (!Number.isFinite(stamp) || stamp <= 0) {
        return "";
      }
      try {
        return new Date(stamp * 1000).toLocaleTimeString("ru-RU");
      } catch (_err) {
        return "";
      }
    };

    const stopLiveLogPolling = () => {
      activeSynthesisJobId = "";
      activeSynthesisRequest = null;
      if (liveLogPollTimer) {
        window.clearTimeout(liveLogPollTimer);
        liveLogPollTimer = 0;
      }
    };

    const renderLiveLog = (payload) => {
      if (!liveLogDetails || !liveLogStatus || !liveLogPercent || !liveLogList || !liveLogProgressBar) {
        return;
      }

      const status = String(payload?.status || "idle");
      const percent = clampPercent(payload?.percent);
      const message = String(payload?.message || messages.liveLogIdle);
      const logs = Array.isArray(payload?.logs) ? payload.logs : [];

      liveLogStatus.textContent = message;
      liveLogPercent.textContent = `${percent}%`;
      liveLogProgressBar.style.width = `${percent}%`;
      liveLogDetails.classList.toggle("is-done", status === "done");
      liveLogDetails.classList.toggle("is-error", status === "error");

      liveLogList.innerHTML = "";
      if (!logs.length) {
        const emptyNode = document.createElement("div");
        emptyNode.className = "wintts-live-log-empty";
        emptyNode.textContent = status === "idle" ? messages.liveLogEmpty : message;
        liveLogList.appendChild(emptyNode);
        if (status === "error" && !liveLogDetails.open) {
          liveLogDetails.open = true;
        }
        return;
      }

      logs.forEach((entry) => {
        const level = String(entry?.level || "info");
        const item = document.createElement("div");
        item.className = `wintts-live-log-entry${level === "warning" ? " is-warning" : ""}${level === "error" ? " is-error" : ""}`;

        const meta = document.createElement("div");
        meta.className = "wintts-live-log-entry-meta";
        const timeText = formatLogTime(entry?.ts);
        const percentText = `${clampPercent(entry?.percent)}%`;
        meta.textContent = [timeText, percentText, getLiveLogStatusLabel(status)].filter(Boolean).join(" • ");

        const body = document.createElement("div");
        body.textContent = String(entry?.message || "");

        item.appendChild(meta);
        item.appendChild(body);
        liveLogList.appendChild(item);
      });

      liveLogList.scrollTop = liveLogList.scrollHeight;
      if (status === "error" && !liveLogDetails.open) {
        liveLogDetails.open = true;
      }
    };

    const appendLiveLogLine = (message, level = "info") => {
      if (!liveLogList || !liveLogStatus || !liveLogPercent) {
        return;
      }
      const cleanMessage = String(message || "").trim();
      if (!cleanMessage) {
        return;
      }
      if (liveLogList.children.length === 1 && liveLogList.firstElementChild?.classList.contains("wintts-live-log-empty")) {
        liveLogList.innerHTML = "";
      }

      const item = document.createElement("div");
      item.className = `wintts-live-log-entry${level === "warning" ? " is-warning" : ""}${level === "error" ? " is-error" : ""}`;

      const meta = document.createElement("div");
      meta.className = "wintts-live-log-entry-meta";
      meta.textContent = `${new Date().toLocaleTimeString("ru-RU")} • ${liveLogPercent.textContent || "0%"}`;

      const body = document.createElement("div");
      body.textContent = cleanMessage;

      item.appendChild(meta);
      item.appendChild(body);
      liveLogList.appendChild(item);
      liveLogStatus.textContent = cleanMessage;
      liveLogList.scrollTop = liveLogList.scrollHeight;
    };

    const queueLiveLogPoll = (jobId, delayMs = 650) => {
      if (!jobId || activeSynthesisJobId !== jobId) {
        return;
      }
      if (liveLogPollTimer) {
        window.clearTimeout(liveLogPollTimer);
      }
      liveLogPollTimer = window.setTimeout(() => {
        pollSynthesisStatus(jobId, activeSynthesisRequest);
      }, delayMs);
    };

    const getNextPollDelay = (status, percent) => {
      if (status === "queued") {
        return 900;
      }
      if (percent >= 95) {
        return 450;
      }
      if (percent >= 70) {
        return 550;
      }
      return 700;
    };

    const renderDiagnostics = (lines, announce = false) => {
      const normalized = toLines(lines);
      if (!normalized.length) {
        diagnosticsBox.textContent = messages.diagnosticsEmpty;
        diagnosticsBox.setAttribute("aria-live", announce ? "assertive" : "polite");
        return;
      }
      diagnosticsBox.textContent = normalized.join("\n");
      diagnosticsBox.setAttribute("aria-live", announce ? "assertive" : "polite");
    };

    const renderInstallLog = (lines, { append = false, open = false } = {}) => {
      if (!installLogOutput) {
        return;
      }
      const incoming = toLines(lines);
      if (!incoming.length && !append) {
        installLogOutput.textContent = "Лог установки появится после запуска.";
        return;
      }
      const current = append ? toLines(installLogOutput.textContent || "") : [];
      const merged = append ? [...current, ...incoming] : incoming;
      installLogOutput.textContent = merged.length
        ? merged.join("\n")
        : "Лог установки появится после запуска.";
      if (installLogDetails && open) {
        installLogDetails.open = true;
      }
    };

    const setInstallLogStatus = (message, mode = "info") => {
      if (!installLogStatus) {
        return;
      }
      installLogStatus.textContent = String(message || messages.installLogIdle);
      installLogStatus.classList.toggle("is-error", mode === "error");
      installLogStatus.classList.toggle("is-ok", mode === "ok");
    };

    const renderHttpFallbackDiagnostics = (response, parseError, rawText) => {
      const lines = [
        `HTTP статус: ${response.status} ${response.statusText || ""}`.trim(),
      ];
      if (parseError) {
        lines.push(`Ошибка разбора JSON: ${parseError}`);
      }
      const snippet = toBodySnippet(rawText);
      if (snippet) {
        lines.push("Ответ сервера (фрагмент):");
        lines.push(snippet);
      }
      lines.push(
        "Похоже, сервер вернул не JSON (возможно, ошибка бэкенда/доступа или редирект на страницу входа)."
      );
      renderDiagnostics(lines, true);
    };

    const isEdgeNormalizerAnalyzerAvailable = (engine = engineSelect.value) => (
      Boolean(config.normalizePreviewUrl)
      && toBool(edgeNormalizerConfig?.available, true)
      && Boolean(String(engine || "").trim())
    );

    const setEdgeNormalizerAnalyzerStatus = (message, isError = false) => {
      if (!edgeNormalizerAnalyzerStatus) {
        return;
      }
      edgeNormalizerAnalyzerStatus.textContent = String(message || "");
      edgeNormalizerAnalyzerStatus.classList.toggle("is-error", Boolean(isError));
    };

    const setEdgeNormalizerRecommendations = (items = []) => {
      if (!edgeNormalizerRecommendations) {
        return;
      }
      edgeNormalizerRecommendations.innerHTML = "";
      const lines = Array.isArray(items)
        ? items.map((item) => String(item || "").trim()).filter(Boolean).slice(0, 8)
        : [];
      if (!lines.length) {
        const li = document.createElement("li");
        li.textContent = "Рекомендации появятся после анализа.";
        edgeNormalizerRecommendations.appendChild(li);
        return;
      }
      lines.forEach((line) => {
        const li = document.createElement("li");
        li.textContent = line;
        edgeNormalizerRecommendations.appendChild(li);
      });
    };

    const getSelectedDropSymbolsFromUI = () => {
      return normalizeDropSymbols(edgeNormalizerSelectedDropSymbols);
    };

    const clearEdgeNormalizerAnalyzer = (statusText = "") => {
      edgeNormalizerLastSymbolDelta = [];
      if (edgeNormalizerPreviewBefore) {
        edgeNormalizerPreviewBefore.value = "";
      }
      if (edgeNormalizerPreviewAfter) {
        edgeNormalizerPreviewAfter.value = "";
      }
      if (edgeNormalizerSymbols) {
        edgeNormalizerSymbols.innerHTML = "";
        const empty = document.createElement("div");
        empty.className = "wintts-normalizer-symbols-empty";
        empty.textContent = "Символы появятся после анализа.";
        edgeNormalizerSymbols.appendChild(empty);
      }
      if (edgeNormalizerReport) {
        edgeNormalizerReport.textContent = "Отчет появится после анализа.";
      }
      setEdgeNormalizerRecommendations([]);
      if (statusText) {
        setEdgeNormalizerAnalyzerStatus(statusText, false);
      }
    };

    const renderEdgeNormalizerSymbols = (deltaItems = []) => {
      if (!edgeNormalizerSymbols) {
        return;
      }
      edgeNormalizerLastSymbolDelta = Array.isArray(deltaItems) ? deltaItems : [];
      const selectedSymbols = normalizeDropSymbols(edgeNormalizerSelectedDropSymbols);
      const normalizedItems = Array.isArray(deltaItems)
        ? deltaItems
          .map((item) => ({
            symbol: String(item?.symbol || ""),
            before: Number(item?.before || 0),
            after: Number(item?.after || 0),
            removed: Number(item?.removed || 0),
          }))
          .filter((item) => item.symbol && isAllowedDropSymbol(item.symbol) && item.before > 0)
        : [];

      edgeNormalizerSymbols.innerHTML = "";
      if (!normalizedItems.length) {
        const empty = document.createElement("div");
        empty.className = "wintts-normalizer-symbols-empty";
        empty.textContent = "Служебные символы не найдены.";
        edgeNormalizerSymbols.appendChild(empty);
        return;
      }

      normalizedItems.forEach((item) => {
        const row = document.createElement("div");
        row.className = "wintts-normalizer-symbol-row";

        const choice = document.createElement("label");
        choice.className = "wintts-checkbox-row wintts-normalizer-symbol-choice";

        const input = document.createElement("input");
        input.type = "checkbox";
        input.setAttribute("data-symbol", item.symbol);
        input.checked = selectedSymbols.includes(item.symbol);

        const text = document.createElement("span");
        text.textContent = `${item.symbol} | до: ${item.before}, после: ${item.after}, удалено: ${item.removed}`;

        input.addEventListener("change", () => {
          const current = normalizeDropSymbols(edgeNormalizerSelectedDropSymbols);
          const next = input.checked
            ? normalizeDropSymbols([...current, item.symbol], current)
            : normalizeDropSymbols(current.filter((symbol) => symbol !== item.symbol));
          setEdgeNormalizerSelectedDropSymbols(next, true);
          markEdgeNormalizerPresetCustom();
          updateEdgeNormalizerSummary();
          updateEdgeControlsForEngine(engineSelect.value);
          queueEdgeNormalizerAnalysis({ delayMs: 260 });
        });

        const removeButton = document.createElement("button");
        removeButton.type = "button";
        removeButton.className = "btn wintts-normalizer-symbol-action";
        removeButton.setAttribute("data-symbol-remove", item.symbol);
        removeButton.textContent = "Удалить все";
        removeButton.addEventListener("click", () => {
          const current = normalizeDropSymbols(edgeNormalizerSelectedDropSymbols);
          if (!current.includes(item.symbol)) {
            setEdgeNormalizerSelectedDropSymbols([...current, item.symbol], true);
          }
          input.checked = true;
          markEdgeNormalizerPresetCustom();
          updateEdgeNormalizerSummary();
          updateEdgeControlsForEngine(engineSelect.value);
          queueEdgeNormalizerAnalysis({ delayMs: 120, force: true });
        });

        choice.appendChild(input);
        choice.appendChild(text);
        row.appendChild(choice);
        row.appendChild(removeButton);
        edgeNormalizerSymbols.appendChild(row);
      });
    };

    const renderEdgeNormalizerReport = (analysis) => {
      if (!edgeNormalizerReport) {
        return;
      }
      const data = analysis && typeof analysis === "object" ? analysis : {};
      const symbols = data.symbols && typeof data.symbols === "object" ? data.symbols : {};
      const delta = Array.isArray(symbols.delta) ? symbols.delta : [];
      const totals = symbols.totals && typeof symbols.totals === "object" ? symbols.totals : {};
      const totalBefore = Number(
        totals.before
        ?? (Array.isArray(symbols.before) ? symbols.before.reduce((sum, item) => sum + Number(item?.count || 0), 0) : 0)
      );
      const totalAfter = Number(
        totals.after
        ?? (Array.isArray(symbols.after) ? symbols.after.reduce((sum, item) => sum + Number(item?.count || 0), 0) : 0)
      );
      const totalRemoved = Number(totals.removed ?? Math.max(0, totalBefore - totalAfter));
      const uniqueBefore = Number(
        totals.unique_before
        ?? (Array.isArray(symbols.before) ? symbols.before.length : 0)
      );
      const uniqueAfter = Number(
        totals.unique_after
        ?? (Array.isArray(symbols.after) ? symbols.after.length : 0)
      );

      const lines = [
        `Всего служебных/пунктуационных символов: до ${totalBefore}, после ${totalAfter}, удалено ${totalRemoved}.`,
        `Уникальных символов: до ${uniqueBefore}, после ${uniqueAfter}.`,
      ];
      if (delta.length) {
        lines.push("Разбивка по символам (до -> после, удалено):");
        delta.slice(0, 36).forEach((item) => {
          const symbol = String(item?.symbol || "").replace(/\s/g, " ");
          const before = Number(item?.before || 0);
          const after = Number(item?.after || 0);
          const removed = Number(item?.removed || 0);
          lines.push(`${symbol}: ${before} -> ${after}, ${removed}`);
        });
      } else {
        lines.push("Символы для отчета не найдены.");
      }
      edgeNormalizerReport.textContent = lines.join("\n");
    };

    const renderEdgeNormalizerAnalysis = (analysis) => {
      const data = analysis && typeof analysis === "object" ? analysis : {};
      const normalizedSettings = normalizeEdgeNormalizerSettings(
        data.settings || getEdgeNormalizerSettings(),
        edgeNormalizerConfig?.default || edgeNormalizerDefaultSettings
      );
      setEdgeNormalizerSelectedDropSymbols(
        normalizedSettings.drop_symbols,
        true
      );

      const preview = data.preview && typeof data.preview === "object" ? data.preview : {};
      if (edgeNormalizerPreviewBefore) {
        edgeNormalizerPreviewBefore.value = String(preview.before || "");
      }
      if (edgeNormalizerPreviewAfter) {
        edgeNormalizerPreviewAfter.value = String(preview.after || "");
      }

      const symbols = data.symbols && typeof data.symbols === "object" ? data.symbols : {};
      renderEdgeNormalizerSymbols(Array.isArray(symbols.delta) ? symbols.delta : []);
      renderEdgeNormalizerReport(data);
      setEdgeNormalizerRecommendations(data.recommendations || []);

      const inputLength = Number(data.input_length || 0);
      const normalizedLength = Number(data.normalized_length || 0);
      const changed = Boolean(data.changed);
      const summary = String(data.summary || "").trim();
      const statusText = summary || `Анализ завершен: ${inputLength} -> ${normalizedLength} символов, изменено: ${changed ? "да" : "нет"}.`;
      setEdgeNormalizerAnalyzerStatus(statusText, false);
    };

    const requestEdgeNormalizerAnalysis = async () => {
      if (!isEdgeNormalizerAnalyzerAvailable(engineSelect.value) || busy) {
        return;
      }
      if (!config.csrfToken || !config.normalizePreviewUrl) {
        setEdgeNormalizerAnalyzerStatus("Отсутствует CSRF-токен. Обновите страницу и попробуйте снова.", true);
        return;
      }
      const text = String(textArea.value || "");
      if (!text.trim()) {
        clearEdgeNormalizerAnalyzer("Вставьте текст для запуска анализатора.");
        return;
      }

      const requestId = ++edgeNormalizerAnalyzeRequestId;
      const requestPayload = {
        text,
        engine: String(engineSelect.value || ""),
        edge_text_normalizer: getEdgeNormalizerSettings(),
        text_normalizer: getEdgeNormalizerSettings(),
        csrf_token: config.csrfToken,
      };
      setEdgeNormalizerAnalyzerStatus(
        `Анализирую текст для ${String(engineSelect.value || "выбранного движка")}...`,
        false
      );

      try {
        const response = await fetch(config.normalizePreviewUrl, {
          method: "POST",
          credentials: "same-origin",
          headers: {
            "Content-Type": "application/json",
            "X-CSRFToken": config.csrfToken,
            Accept: "application/json",
          },
          body: JSON.stringify(requestPayload),
        });
        const { data, rawText, parseError } = await parseJsonResponse(response);
        if (requestId < edgeNormalizerAnalyzeRequestId) {
          return;
        }

        if (!data) {
          setEdgeNormalizerAnalyzerStatus("Ответ анализатора сервера не является валидным JSON.", true);
          renderHttpFallbackDiagnostics(response, parseError, rawText);
          return;
        }
        if (!response.ok || !data.ok) {
          setEdgeNormalizerAnalyzerStatus(data.error || "Запрос к анализатору завершился ошибкой.", true);
          const lines = toLines(data.diagnostics_lines);
          if (lines.length) {
            renderDiagnostics(lines, true);
          }
          return;
        }

        renderEdgeNormalizerAnalysis(data.analysis || {});
      } catch (err) {
        if (requestId < edgeNormalizerAnalyzeRequestId) {
          return;
        }
        const message = err && err.message
          ? `Сетевая ошибка анализатора: ${String(err.message)}`
          : "Сетевая ошибка анализатора.";
        setEdgeNormalizerAnalyzerStatus(message, true);
      }
    };

    const queueEdgeNormalizerAnalysis = ({ delayMs = 380, force = false } = {}) => {
      if (!isEdgeNormalizerAnalyzerAvailable(engineSelect.value)) {
        return;
      }
      if (!force && !toBool(edgeNormalizerAnalyzerAuto?.checked, true)) {
        return;
      }
      if (edgeNormalizerAnalyzeTimer) {
        window.clearTimeout(edgeNormalizerAnalyzeTimer);
      }
      edgeNormalizerAnalyzeTimer = window.setTimeout(() => {
        edgeNormalizerAnalyzeTimer = 0;
        requestEdgeNormalizerAnalysis();
      }, Math.max(0, Number(delayMs || 0)));
    };
    const formatNumber = (value) => {
      const parsed = Number(value || 0);
      if (!Number.isFinite(parsed)) {
        return "0";
      }
      return new Intl.NumberFormat("ru-RU").format(parsed);
    };

    const formatBytes = (value) => {
      const parsed = Number(value || 0);
      if (!Number.isFinite(parsed) || parsed <= 0) {
        return "0 Б";
      }
      const units = ["Б", "КБ", "МБ", "ГБ"];
      let size = parsed;
      let unitIndex = 0;
      while (size >= 1024 && unitIndex < units.length - 1) {
        size /= 1024;
        unitIndex += 1;
      }
      if (unitIndex === 0) {
        return `${Math.round(size)} ${units[unitIndex]}`;
      }
      return `${size.toFixed(1)} ${units[unitIndex]}`;
    };

    const resetSourceMeta = () => {
      currentSourceMeta = {
        sourceName: "",
        sourceExt: "",
        title: "",
        charCount: 0,
        sizeBytes: 0,
      };
    };

    const updateFileInfo = (message = "", isError = false) => {
      if (!fileInfoBox) {
        return;
      }

      if (message) {
        fileInfoBox.textContent = message;
        fileInfoBox.classList.toggle("is-error", Boolean(isError));
        fileInfoBox.classList.toggle("is-ok", !isError);
        return;
      }

      const file = fileInput?.files && fileInput.files[0] ? fileInput.files[0] : null;
      if (currentSourceMeta.sourceName) {
        const titleLabel = currentSourceMeta.title && currentSourceMeta.title !== currentSourceMeta.sourceName
          ? ` | ${currentSourceMeta.title}`
          : "";
        fileInfoBox.textContent = `Загружен источник: ${currentSourceMeta.sourceName}${titleLabel} | ${formatNumber(currentSourceMeta.charCount)} симв.`;
        fileInfoBox.classList.remove("is-error");
        fileInfoBox.classList.add("is-ok");
        return;
      }
      if (file) {
        fileInfoBox.textContent = `Выбран файл: ${file.name} | ${formatBytes(file.size)}. Нажмите «Загрузить текст из файла».`;
        fileInfoBox.classList.remove("is-error");
        fileInfoBox.classList.remove("is-ok");
        return;
      }
      fileInfoBox.textContent = "Файл не выбран.";
      fileInfoBox.classList.remove("is-error");
      fileInfoBox.classList.remove("is-ok");
    };

    const buildHistoryItem = (requestMeta, responseData = {}) => {
      const sourceName = requestMeta.sourceName || "";
      const engineName = String(responseData.engine || requestMeta.engine || "");
      const voiceName = String(responseData.voice || requestMeta.voice || "");
      const parallelMode = supportsParallelism(engineName);
      const googleRetryMode = supportsGoogleRetry(engineName);
      const languageMode = supportsLanguageSelector(engineName);
      const edgeNumeric = (value, fallback = 0) => parseEdgeDefault(value, fallback);
      const defaultParallelConfig = getParallelismConfig(engineName);
      const parallelValue = parallelMode
        ? Number(
          responseData.google_parallelism
          ?? responseData.edge_parallelism
          ?? requestMeta.googleParallelism
          ?? requestMeta.edgeParallelism
          ?? defaultParallelConfig.current
        )
        : null;
      const edgeLanguage = languageMode
        ? String(
          responseData.edge_language
          || requestMeta.edgeLanguage
          || getEdgeVoiceLanguage(voiceName, engineName)
          || ""
        )
        : "";
      const inlineText = requestMeta.text.length <= 20000 ? requestMeta.text : "";
      const edgeTextNormalizer = supportsTextNormalizer(engineName)
        ? normalizeEdgeNormalizerSettings(
          responseData.text_normalizer
          || responseData.edge_text_normalizer
          || requestMeta.edgeTextNormalizer
          || {},
          edgeNormalizerConfig?.default || edgeNormalizerDefaultSettings
        )
        : null;
      const dualLanguagePayload = responseData.dual_language && typeof responseData.dual_language === "object"
        ? responseData.dual_language
        : {};
      const dualLanguageEnabled = toBool(
        dualLanguagePayload.enabled,
        toBool(requestMeta.dualLanguageEnabled, false)
      );
      const secondaryLanguage = String(
        dualLanguagePayload.secondary_language
        || requestMeta.secondaryLanguage
        || ""
      );
      const secondaryVoice = String(
        dualLanguagePayload.secondary_voice
        || requestMeta.secondaryVoice
        || ""
      );
      const dualPausePayload = dualLanguagePayload.pause_normalization && typeof dualLanguagePayload.pause_normalization === "object"
        ? dualLanguagePayload.pause_normalization
        : {};
      const dualPauseMode = normalizeDualPauseMode(
        dualLanguagePayload.pause_mode
        || dualPausePayload.mode
        || requestMeta.dualPauseMode
        || dualPauseConfig.default_mode
      );
      const dualPauseMs = clampDualPauseMs(
        dualLanguagePayload.pause_ms
        ?? dualPausePayload.requested_ms
        ?? requestMeta.dualPauseMs
        ?? dualPauseConfig.ms?.default
      );
      const dualPauseApplied = toBool(dualPausePayload.applied, false);
      const dualPauseEffectiveAvg = Number(dualPausePayload.target_pause_ms_avg ?? 0);
      return {
        ts: Math.floor(Date.now() / 1000),
        engine: engineName,
        voice: voiceName,
        text: inlineText,
        textSnippet: toBodySnippet(requestMeta.text, 220),
        textCharCount: requestMeta.text.length,
        sourceName,
        sourceTitle: requestMeta.sourceTitle || "",
        sourceExt: requestMeta.sourceExt || "",
        edgeLanguage: languageMode ? edgeLanguage : null,
        edgeParallelism: parallelValue,
        googleParallelism: isGoogleEngine(engineName) ? parallelValue : null,
        googleRetryCount: googleRetryMode
          ? Number(responseData.google_retry_count ?? requestMeta.googleRetryCount ?? googleRetryConfig.current)
          : null,
        edgeRate: edgeNumeric(responseData.edge_rate, requestMeta.edgeRate ?? 0),
        edgePitch: edgeNumeric(responseData.edge_pitch, requestMeta.edgePitch ?? 0),
        edgeVolume: edgeNumeric(responseData.edge_volume, requestMeta.edgeVolume ?? 0),
        dualLanguageEnabled,
        secondaryLanguage: dualLanguageEnabled ? secondaryLanguage : "",
        secondaryVoice: dualLanguageEnabled ? secondaryVoice : "",
        secondaryEdgeRate: edgeNumeric(
          dualLanguagePayload.secondary_edge_rate,
          requestMeta.secondaryEdgeRate ?? requestMeta.edgeRate ?? 0
        ),
        secondaryEdgePitch: edgeNumeric(
          dualLanguagePayload.secondary_edge_pitch,
          requestMeta.secondaryEdgePitch ?? requestMeta.edgePitch ?? 0
        ),
        secondaryEdgeVolume: edgeNumeric(
          dualLanguagePayload.secondary_edge_volume,
          requestMeta.secondaryEdgeVolume ?? requestMeta.edgeVolume ?? 0
        ),
        dualPauseMode,
        dualPauseMs,
        dualPauseApplied,
        dualPauseEffectiveAvg: Number.isFinite(dualPauseEffectiveAvg) ? dualPauseEffectiveAvg : 0,
        edgeTextNormalizer,
        audioUrl: responseData.audio_url,
        downloadUrl: responseData.download_url,
        filename: responseData.filename || "",
      };
    };

    const importSelectedFile = async ({ announce = true } = {}) => {
      const file = fileInput?.files && fileInput.files[0] ? fileInput.files[0] : null;
      if (!file) {
        updateFileInfo("Файл не выбран.", true);
        if (announce) {
          setOutput("Сначала выберите файл книги или документа.", false);
        }
        return null;
      }
      if (!config.importUrl) {
        if (announce) {
          setOutput("Маршрут импорта файла недоступен на сервере.", false);
        }
        return null;
      }
      if (!config.csrfToken) {
        setOutput(messages.csrfMissing, false);
        return null;
      }

      const formData = new FormData();
      formData.append("file", file, file.name);
      formData.append("csrf_token", config.csrfToken);

      if (announce) {
        setOutput(`Загружаю текст из файла ${file.name}...`, true);
      }
      updateFileInfo(`Импортирую файл: ${file.name}...`, false);

      const response = await fetch(config.importUrl, {
        method: "POST",
        credentials: "same-origin",
        headers: {
          "X-CSRFToken": config.csrfToken,
          Accept: "application/json",
        },
        body: formData,
      });

      const { data, rawText, parseError } = await parseJsonResponse(response);
      if (!data) {
        renderHttpFallbackDiagnostics(response, parseError, rawText);
        updateFileInfo("Не удалось получить ответ сервера при импорте файла.", true);
        if (announce) {
          setOutput("Импорт файла завершился ошибкой.", false);
        }
        return null;
      }

      if (!response.ok || !data.ok) {
        const lines = toLines(data.diagnostics_lines);
        if (lines.length) {
          renderDiagnostics(lines, true);
        } else if (!response.ok) {
          renderHttpFallbackDiagnostics(response, "", rawText);
        }
        updateFileInfo(data.error || "Не удалось импортировать файл.", true);
        if (announce) {
          setOutput(data.error || "Не удалось импортировать файл.", false);
        }
        return null;
      }

      textArea.value = String(data.text || "");
      currentSourceMeta = {
        sourceName: String(data.source_name || file.name || ""),
        sourceExt: String(data.source_ext || ""),
        title: String(data.title || ""),
        charCount: Number(data.char_count || textArea.value.length || 0),
        sizeBytes: Number(data.size_bytes || file.size || 0),
      };
      updateFileInfo();
      renderDiagnostics(data.diagnostics_lines || [], false);
      if (announce) {
        const titleLabel = currentSourceMeta.title && currentSourceMeta.title !== currentSourceMeta.sourceName
          ? ` (${currentSourceMeta.title})`
          : "";
        setOutput(`Текст из файла ${currentSourceMeta.sourceName}${titleLabel} загружен. Символов: ${formatNumber(currentSourceMeta.charCount)}.`, true);
      }
      queueEdgeNormalizerAnalysis({ delayMs: 120 });
      return data;
    };

    const ensureTextAvailable = async () => {
      const currentText = textArea.value.trim();
      if (currentText) {
        return currentText;
      }
      const file = fileInput?.files && fileInput.files[0] ? fileInput.files[0] : null;
      if (!file) {
        return "";
      }
      const imported = await importSelectedFile({ announce: false });
      if (!imported || !imported.text) {
        return "";
      }
      return String(imported.text || "").trim();
    };

    const getRequestMeta = (text) => {
      const edgeValues = getEdgeProsodyValues();
      const secondaryValues = getSecondaryProsodyValues();
      const engine = engineSelect.value;
      const edgeLanguage = supportsLanguageSelector(engine) ? getEdgeLanguageValue(engine) : "";
      const dualMode = isDualLanguageEnabled(engine);
      const secondaryLanguage = dualMode ? getSecondaryLanguageValue(engine) : "";
      const secondaryVoice = dualMode ? String(secondaryVoiceSelect?.value || "").trim() : "";
      const dualPauseSettings = getDualPauseSettings();
      const parallelismValue = supportsParallelism(engine) ? getEdgeParallelismValue(engine) : null;
      const edgeTextNormalizer = supportsTextNormalizer(engine)
        ? getEdgeNormalizerSettings()
        : null;
      return {
        text,
        engine,
        voice: voiceSelect.value,
        edgeLanguage: edgeLanguage || null,
        edgeParallelism: isEdgeEngine(engine) ? parallelismValue : null,
        googleParallelism: isGoogleEngine(engine) ? parallelismValue : null,
        googleRetryCount: supportsGoogleRetry(engine) ? getGoogleRetryCountValue() : null,
        edgeRate: edgeValues.edgeRate,
        edgePitch: edgeValues.edgePitch,
        edgeVolume: edgeValues.edgeVolume,
        dualLanguageEnabled: dualMode,
        secondaryLanguage: secondaryLanguage || null,
        secondaryVoice: secondaryVoice || null,
        secondaryEdgeRate: secondaryValues.edgeRate,
        secondaryEdgePitch: secondaryValues.edgePitch,
        secondaryEdgeVolume: secondaryValues.edgeVolume,
        dualPauseMode: dualPauseSettings.mode,
        dualPauseMs: dualPauseSettings.pauseMs,
        edgeTextNormalizer,
        sourceName: currentSourceMeta.sourceName || (fileInput?.files && fileInput.files[0] ? fileInput.files[0].name : ""),
        sourceTitle: currentSourceMeta.title || "",
        sourceExt: currentSourceMeta.sourceExt || "",
      };
    };

    const setBusy = (state) => {
      busy = state;
      runButton.disabled = state;
      runButton.setAttribute("aria-disabled", state ? "true" : "false");
      root.classList.toggle("is-busy", state);
      clearButton.disabled = state;
      if (fileInput) {
        fileInput.disabled = state;
      }
      if (importFileButton) {
        importFileButton.disabled = state;
      }
      if (clearFileButton) {
        clearFileButton.disabled = state;
      }
      if (edgeParallelismSelect) {
        edgeParallelismSelect.disabled = state;
      }
      if (googleRetrySelect) {
        googleRetrySelect.disabled = state;
      }
      if (edgeLanguageSelect) {
        edgeLanguageSelect.disabled = state;
      }
      if (dualLanguageEnabled) {
        dualLanguageEnabled.disabled = state;
      }
      if (secondaryLanguageSelect) {
        secondaryLanguageSelect.disabled = state;
      }
      if (secondaryVoiceSelect) {
        secondaryVoiceSelect.disabled = state;
      }
      if (edgeRateInput) {
        edgeRateInput.disabled = state;
      }
      if (edgePitchInput) {
        edgePitchInput.disabled = state;
      }
      if (edgeVolumeInput) {
        edgeVolumeInput.disabled = state;
      }
      if (secondaryRateInput) {
        secondaryRateInput.disabled = state;
      }
      if (secondaryPitchInput) {
        secondaryPitchInput.disabled = state;
      }
      if (secondaryVolumeInput) {
        secondaryVolumeInput.disabled = state;
      }
      if (dualPauseModeSelect) {
        dualPauseModeSelect.disabled = state;
      }
      if (dualPauseMsInput) {
        dualPauseMsInput.disabled = state;
      }
      [
        edgeNormalizerEnabled,
        edgeNormalizerPreset,
        edgeNormalizerAuto,
        edgeNormalizerStripMarkdown,
        edgeNormalizerStripUrls,
        edgeNormalizerStripEmails,
        edgeNormalizerCollapseSymbols,
        edgeNormalizerCollapsePunctuation,
        edgeNormalizerDropSymbolTokens,
        edgeNormalizerWhitespace,
      ].forEach((control) => {
        if (control) {
          control.disabled = state;
        }
      });
      if (edgeNormalizerAnalyzerAuto) {
        edgeNormalizerAnalyzerAuto.disabled = state;
      }
      if (edgeNormalizerAnalyzeButton) {
        edgeNormalizerAnalyzeButton.disabled = state;
      }
      if (edgeNormalizerSymbols) {
        edgeNormalizerSymbols.querySelectorAll("input[type='checkbox'][data-symbol]").forEach((control) => {
          control.disabled = state;
        });
      }
      if (installButton) {
        installButton.disabled = state;
      }
      output.setAttribute("aria-busy", state ? "true" : "false");
      diagnosticsBox.setAttribute("aria-busy", state ? "true" : "false");
      if (liveLogList) {
        liveLogList.setAttribute("aria-busy", state ? "true" : "false");
      }
    };

    const loadHistory = () => {
      try {
        const raw = localStorage.getItem(historyKey);
        const parsed = raw ? JSON.parse(raw) : [];
        return Array.isArray(parsed) ? parsed : [];
      } catch (_err) {
        historyAvailable = false;
        return [];
      }
    };

    const saveHistory = (items) => {
      try {
        localStorage.setItem(historyKey, JSON.stringify(items));
      } catch (_err) {
        historyAvailable = false;
      }
    };

    const isEdgeEngine = (engine) => String(engine || "") === "Edge TTS";
    const isGoogleEngine = (engine) => String(engine || "") === "Google";
    const getPreferredEngine = (engines, fallback = "") => {
      const list = Array.isArray(engines)
        ? engines.map((item) => String(item || "").trim()).filter(Boolean)
        : [];
      const preferredOrder = ["Edge TTS", "Google", "RHVoice", "pyx3"];
      for (const candidate of preferredOrder) {
        if (list.includes(candidate)) {
          return candidate;
        }
      }
      return list[0] || String(fallback || "");
    };

    const normalizeEdgeLanguageCode = (value) => {
      const clean = String(value || "").trim().toLowerCase();
      if (!clean) {
        return "und";
      }
      const match = clean.match(/^[a-z]{2,3}/);
      return match ? match[0] : "und";
    };

    const parseEdgeLanguageFromVoice = (voiceLabel) => {
      const text = String(voiceLabel || "").trim();
      if (!text) {
        return "und";
      }
      const direct = text.match(/^([a-z]{2,3})[-_][a-z0-9]{2,8}/i);
      if (direct && direct[1]) {
        return normalizeEdgeLanguageCode(direct[1]);
      }
      const inBrackets = text.match(/\(([a-z]{2,3})(?:[-_][a-z0-9]{2,8})?/i);
      if (inBrackets && inBrackets[1]) {
        return normalizeEdgeLanguageCode(inBrackets[1]);
      }
      const inText = text.match(/([a-z]{2,3})[-_][a-z0-9]{2,8}/i);
      if (inText && inText[1]) {
        return normalizeEdgeLanguageCode(inText[1]);
      }
      return "und";
    };

    const edgeLanguageSortKey = (languageCode) => {
      const code = normalizeEdgeLanguageCode(languageCode);
      if (code === "ru") {
        return `0:${code}`;
      }
      if (code === "uk") {
        return `1:${code}`;
      }
      return `2:${code}`;
    };

    const formatEdgeLanguageLabel = (languageCode) => {
      const code = normalizeEdgeLanguageCode(languageCode);
      if (code === "und") {
        return "Прочие (und)";
      }
      let name = "";
      try {
        name = languageDisplayNames ? String(languageDisplayNames.of(code) || "") : "";
      } catch (_err) {
        name = "";
      }
      if (!name) {
        return code;
      }
      return `${name.charAt(0).toUpperCase()}${name.slice(1)} (${code})`;
    };

    const getEngineVoiceCatalog = (engine) => {
      const key = String(engine || "");
      const catalog = voiceCatalogByEngine?.[key];
      if (catalog && typeof catalog === "object") {
        return catalog;
      }
      return {};
    };

    const setEdgeVoiceCatalog = (rawCatalog, allVoicesByEngine = {}) => {
      const nextCatalogByEngine = {};
      const nextVoiceToLanguageByEngine = {};
      const engineNames = new Set([
        ...Object.keys(allVoicesByEngine || {}),
        ...Object.keys(rawCatalog || {}),
      ]);

      engineNames.forEach((engineName) => {
        const engine = String(engineName || "").trim();
        if (!engine) {
          return;
        }

        const allVoices = Array.isArray(allVoicesByEngine?.[engine])
          ? allVoicesByEngine[engine].map((item) => String(item || "").trim()).filter(Boolean)
          : [];
        const availableVoices = new Set(allVoices);
        const nextCatalog = {};
        const nextVoiceToLanguage = {};

        const appendVoice = (languageCode, voiceLabel) => {
          const code = normalizeEdgeLanguageCode(languageCode);
          const voice = String(voiceLabel || "").trim();
          if (!voice) {
            return;
          }
          if (availableVoices.size && !availableVoices.has(voice)) {
            return;
          }
          if (!nextCatalog[code]) {
            nextCatalog[code] = [];
          }
          if (!nextCatalog[code].includes(voice)) {
            nextCatalog[code].push(voice);
          }
          nextVoiceToLanguage[voice] = code;
        };

        const engineRawCatalog = rawCatalog?.[engine];
        if (engineRawCatalog && typeof engineRawCatalog === "object") {
          Object.entries(engineRawCatalog).forEach(([languageCode, voiceItems]) => {
            if (!Array.isArray(voiceItems)) {
              return;
            }
            voiceItems.forEach((voiceLabel) => appendVoice(languageCode, voiceLabel));
          });
        }

        allVoices.forEach((voiceLabel) => {
          if (nextVoiceToLanguage[voiceLabel]) {
            return;
          }
          appendVoice(parseEdgeLanguageFromVoice(voiceLabel), voiceLabel);
        });

        const ordered = {};
        Object.keys(nextCatalog)
          .sort((a, b) => edgeLanguageSortKey(a).localeCompare(edgeLanguageSortKey(b), "ru"))
          .forEach((languageCode) => {
            const voices = (nextCatalog[languageCode] || []).filter(Boolean);
            if (voices.length) {
              ordered[languageCode] = voices;
            }
          });

        nextCatalogByEngine[engine] = ordered;
        nextVoiceToLanguageByEngine[engine] = nextVoiceToLanguage;
      });

      voiceCatalogByEngine = nextCatalogByEngine;
      voiceToLanguageByEngine = nextVoiceToLanguageByEngine;
    };

    const getEdgeVoiceLanguage = (voiceLabel, engine = engineSelect.value) => {
      const voice = String(voiceLabel || "").trim();
      if (!voice) {
        return "";
      }
      const engineName = String(engine || "");
      const engineMap = voiceToLanguageByEngine?.[engineName] || {};
      if (engineMap[voice]) {
        return engineMap[voice];
      }
      return parseEdgeLanguageFromVoice(voice);
    };

    const getEdgeVoicesForLanguage = (languageCode, engine = engineSelect.value) => {
      const code = normalizeEdgeLanguageCode(languageCode);
      const voices = getEngineVoiceCatalog(engine)?.[code];
      if (Array.isArray(voices) && voices.length) {
        return voices;
      }
      return [];
    };

    const renderEdgeLanguageOptions = (selectedLanguage = "", selectedVoice = "", engine = engineSelect.value) => {
      if (!edgeLanguageSelect) {
        return "";
      }
      while (edgeLanguageSelect.options.length) {
        edgeLanguageSelect.remove(0);
      }
      const catalog = getEngineVoiceCatalog(engine);
      const languages = Object.keys(catalog || {});
      if (!languages.length) {
        return "";
      }
      let targetLanguage = normalizeEdgeLanguageCode(selectedLanguage);
      if (!languages.includes(targetLanguage)) {
        targetLanguage = getEdgeVoiceLanguage(selectedVoice, engine);
      }
      if (!languages.includes(targetLanguage)) {
        targetLanguage = languages[0];
      }
      languages.forEach((languageCode) => {
        const option = document.createElement("option");
        option.value = languageCode;
        option.textContent = formatEdgeLanguageLabel(languageCode);
        edgeLanguageSelect.appendChild(option);
      });
      edgeLanguageSelect.value = targetLanguage;
      return targetLanguage;
    };

    const renderEdgeVoices = (languageCode, selectedVoice = "", engine = engineSelect.value) => {
      clearSelect(voiceSelect);
      const voices = getEdgeVoicesForLanguage(languageCode, engine);
      voices.forEach((voice) => {
        const option = document.createElement("option");
        option.value = voice;
        option.textContent = voice;
        voiceSelect.appendChild(option);
      });
      if (!voices.length) {
        return "";
      }
      const targetVoice = selectedVoice && voices.includes(selectedVoice) ? selectedVoice : voices[0];
      voiceSelect.value = targetVoice;
      return targetVoice;
    };

    const getEdgeLanguageValue = (engine = engineSelect.value) => {
      const catalog = getEngineVoiceCatalog(engine);
      const languages = Object.keys(catalog || {});
      if (!languages.length) {
        return "";
      }
      if (!edgeLanguageSelect) {
        return languages[0];
      }
      const value = normalizeEdgeLanguageCode(edgeLanguageSelect.value);
      return catalog[value] ? value : languages[0];
    };

    const supportsDualLanguage = (engine = engineSelect.value) => {
      if (!supportsLanguageSelector(engine)) {
        return false;
      }
      const catalog = getEngineVoiceCatalog(engine);
      const voices = Array.isArray(voicesByEngine?.[engine])
        ? voicesByEngine[engine].map((item) => String(item || "").trim()).filter(Boolean)
        : [];
      if (voices.length < 2) {
        return false;
      }
      return Object.keys(catalog || {}).length > 0;
    };

    const isDualLanguageEnabled = (engine = engineSelect.value) => (
      supportsDualLanguage(engine) && toBool(dualLanguageEnabled?.checked, false)
    );

    const getSecondaryVoiceOptions = (
      languageCode,
      engine = engineSelect.value,
      primaryVoice = voiceSelect?.value || ""
    ) => {
      const voices = getEdgeVoicesForLanguage(languageCode, engine);
      const filtered = voices.filter((item) => String(item || "").trim() && String(item || "").trim() !== String(primaryVoice || "").trim());
      return filtered.length ? filtered : voices;
    };

    const renderSecondaryLanguageOptions = (
      selectedLanguage = "",
      selectedVoice = "",
      engine = engineSelect.value
    ) => {
      if (!secondaryLanguageSelect) {
        return "";
      }
      while (secondaryLanguageSelect.options.length) {
        secondaryLanguageSelect.remove(0);
      }
      const catalog = getEngineVoiceCatalog(engine);
      const languages = Object.keys(catalog || {});
      if (!languages.length) {
        return "";
      }
      let targetLanguage = normalizeEdgeLanguageCode(selectedLanguage);
      if (!languages.includes(targetLanguage)) {
        targetLanguage = getEdgeVoiceLanguage(selectedVoice, engine);
      }
      if (!languages.includes(targetLanguage)) {
        const primaryLanguage = getEdgeLanguageValue(engine);
        const alternativeLanguage = languages.find((item) => item !== primaryLanguage);
        targetLanguage = alternativeLanguage || languages[0];
      }
      languages.forEach((languageCode) => {
        const option = document.createElement("option");
        option.value = languageCode;
        option.textContent = formatEdgeLanguageLabel(languageCode);
        secondaryLanguageSelect.appendChild(option);
      });
      secondaryLanguageSelect.value = targetLanguage;
      return targetLanguage;
    };

    const renderSecondaryVoices = (
      languageCode,
      selectedVoice = "",
      engine = engineSelect.value,
      primaryVoice = voiceSelect?.value || ""
    ) => {
      if (!secondaryVoiceSelect) {
        return "";
      }
      while (secondaryVoiceSelect.options.length) {
        secondaryVoiceSelect.remove(0);
      }
      const voices = getSecondaryVoiceOptions(languageCode, engine, primaryVoice);
      voices.forEach((voice) => {
        const option = document.createElement("option");
        option.value = voice;
        option.textContent = voice;
        secondaryVoiceSelect.appendChild(option);
      });
      if (!voices.length) {
        return "";
      }
      const targetVoice = selectedVoice && voices.includes(selectedVoice) ? selectedVoice : voices[0];
      secondaryVoiceSelect.value = targetVoice;
      return targetVoice;
    };

    const getSecondaryLanguageValue = (engine = engineSelect.value) => {
      const catalog = getEngineVoiceCatalog(engine);
      const languages = Object.keys(catalog || {});
      if (!languages.length) {
        return "";
      }
      if (!secondaryLanguageSelect) {
        return languages[0];
      }
      const value = normalizeEdgeLanguageCode(secondaryLanguageSelect.value);
      return catalog[value] ? value : languages[0];
    };

    const getSecondaryProsodyValues = () => ({
      edgeRate: isProsodyEnabled("rate") ? clampEdgeOption("rate", secondaryRateInput?.value ?? 0) : 0,
      edgePitch: isProsodyEnabled("pitch") ? clampEdgeOption("pitch", secondaryPitchInput?.value ?? 0) : 0,
      edgeVolume: isProsodyEnabled("volume") ? clampEdgeOption("volume", secondaryVolumeInput?.value ?? 0) : 0,
    });

    const normalizeDualPauseMode = (value, fallback = dualPauseConfig.default_mode || "auto") => {
      const cleanFallback = ["auto", "manual", "off"].includes(String(fallback || "").trim().toLowerCase())
        ? String(fallback || "").trim().toLowerCase()
        : "auto";
      const clean = String(value || "").trim().toLowerCase();
      const aliases = {
        auto: "auto",
        automatic: "auto",
        smart: "auto",
        manual: "manual",
        custom: "manual",
        fixed: "manual",
        off: "off",
        disabled: "off",
        none: "off",
      };
      const normalized = aliases[clean] || clean;
      return ["auto", "manual", "off"].includes(normalized) ? normalized : cleanFallback;
    };

    const clampDualPauseMs = (value, fallback = dualPauseConfig.ms?.default ?? 90) => {
      const min = Number(dualPauseConfig.ms?.min ?? 0);
      const max = Number(dualPauseConfig.ms?.max ?? 1500);
      const defaultValue = Number(fallback);
      const parsed = Number(String(value ?? "").replace(",", "."));
      const numeric = Number.isFinite(parsed) ? parsed : (Number.isFinite(defaultValue) ? defaultValue : 90);
      return Math.max(min, Math.min(max, Math.round(numeric)));
    };

    const getDualPauseSettings = () => {
      const mode = normalizeDualPauseMode(dualPauseModeSelect?.value, dualPauseConfig.default_mode || "auto");
      const pauseMs = clampDualPauseMs(dualPauseMsInput?.value, dualPauseConfig.ms?.default ?? 90);
      return {
        mode,
        pauseMs,
      };
    };

    const setDualPauseSettings = (settings = {}) => {
      const nextMode = normalizeDualPauseMode(
        settings.mode ?? settings.pause_mode ?? settings.dual_pause_mode,
        dualPauseConfig.default_mode || "auto"
      );
      const nextMs = clampDualPauseMs(
        settings.pauseMs ?? settings.pause_ms ?? settings.dual_pause_ms,
        dualPauseConfig.ms?.default ?? 90
      );
      if (dualPauseModeSelect) {
        const hasMode = Array.from(dualPauseModeSelect.options).some((item) => item.value === nextMode);
        dualPauseModeSelect.value = hasMode ? nextMode : normalizeDualPauseMode(dualPauseConfig.default_mode || "auto");
      }
      if (dualPauseMsInput) {
        dualPauseMsInput.value = String(nextMs);
      }
    };

    const renderDualPauseModes = (selectedMode = dualPauseConfig.default_mode || "auto") => {
      if (!dualPauseModeSelect) {
        return;
      }
      const modes = Array.isArray(dualPauseConfig.modes) && dualPauseConfig.modes.length
        ? dualPauseConfig.modes
        : [
          { id: "auto", label: "Авто" },
          { id: "manual", label: "Ручная" },
          { id: "off", label: "Без нормализации" },
        ];
      while (dualPauseModeSelect.options.length) {
        dualPauseModeSelect.remove(0);
      }
      modes.forEach((item) => {
        const id = normalizeDualPauseMode(item?.id || item?.value || "");
        if (!id) {
          return;
        }
        const option = document.createElement("option");
        option.value = id;
        option.textContent = String(item?.label || id);
        dualPauseModeSelect.appendChild(option);
      });
      if (!dualPauseModeSelect.options.length) {
        ["auto", "manual", "off"].forEach((id) => {
          const option = document.createElement("option");
          option.value = id;
          option.textContent = id;
          dualPauseModeSelect.appendChild(option);
        });
      }
      const normalized = normalizeDualPauseMode(selectedMode, dualPauseConfig.default_mode || "auto");
      const hasValue = Array.from(dualPauseModeSelect.options).some((item) => item.value === normalized);
      dualPauseModeSelect.value = hasValue ? normalized : dualPauseModeSelect.options[0].value;
    };

    const applyDualPauseConfig = (rawConfig = {}) => {
      const source = rawConfig && typeof rawConfig === "object" ? rawConfig : {};
      dualPauseConfig = {
        available: toBool(source.available, true),
        default_mode: normalizeDualPauseMode(source.default_mode || source.mode || "auto"),
        modes: Array.isArray(source.modes) ? source.modes : dualPauseConfig.modes,
        ms: {
          min: Number(source?.ms?.min ?? dualPauseConfig.ms?.min ?? 0),
          max: Number(source?.ms?.max ?? dualPauseConfig.ms?.max ?? 1500),
          default: Number(source?.ms?.default ?? dualPauseConfig.ms?.default ?? 90),
        },
        auto: {
          hint: String(source?.auto?.hint || dualPauseConfig?.auto?.hint || ""),
        },
      };
      renderDualPauseModes(dualPauseConfig.default_mode);
      if (dualPauseMsInput) {
        dualPauseMsInput.min = String(dualPauseConfig.ms.min);
        dualPauseMsInput.max = String(dualPauseConfig.ms.max);
        if (!dualPauseMsInput.value) {
          dualPauseMsInput.value = String(clampDualPauseMs(undefined, dualPauseConfig.ms.default));
        } else {
          dualPauseMsInput.value = String(clampDualPauseMs(dualPauseMsInput.value, dualPauseConfig.ms.default));
        }
      }
      setDualPauseSettings({
        mode: dualPauseModeSelect?.value || dualPauseConfig.default_mode,
        pauseMs: dualPauseMsInput?.value || dualPauseConfig.ms.default,
      });
    };

    const getParallelismConfig = (engine = engineSelect.value) => {
      if (isGoogleEngine(engine)) {
        return googleParallelismConfig || edgeParallelismConfig;
      }
      return edgeParallelismConfig;
    };

    const clampParallelism = (value, engine = engineSelect.value) => {
      const activeConfig = getParallelismConfig(engine);
      const min = Number(activeConfig.min || 1);
      const max = Number(activeConfig.max || 12);
      const fallback = Number(activeConfig.current || activeConfig.default || min);
      const parsed = Number(value);
      if (!Number.isFinite(parsed)) {
        return fallback;
      }
      if (parsed < min) {
        return min;
      }
      if (parsed > max) {
        return max;
      }
      return parsed;
    };

    const getEdgeParallelismValue = (engine = engineSelect.value) => {
      if (!edgeParallelismSelect) {
        return clampParallelism(getParallelismConfig(engine).current, engine);
      }
      return clampParallelism(edgeParallelismSelect.value, engine);
    };

    const clampGoogleRetryCount = (value) => {
      const min = Number(googleRetryConfig.min || 0);
      const max = Number(googleRetryConfig.max || 8);
      const fallback = Number(googleRetryConfig.current || googleRetryConfig.default || min);
      const parsed = Number(value);
      if (!Number.isFinite(parsed)) {
        return fallback;
      }
      if (parsed < min) {
        return min;
      }
      if (parsed > max) {
        return max;
      }
      return Math.round(parsed);
    };

    const getGoogleRetryCountValue = () => {
      if (!googleRetrySelect) {
        return clampGoogleRetryCount(googleRetryConfig.current);
      }
      return clampGoogleRetryCount(googleRetrySelect.value);
    };

    const parseEdgeDefault = (value, fallback = 0) => {
      const text = String(value ?? "").replace("%", "").replace("Hz", "").trim();
      const parsed = Number(text);
      if (!Number.isFinite(parsed)) {
        return fallback;
      }
      return Math.round(parsed);
    };

    const clampEdgeOption = (name, value) => {
      const configItem = edgeOptionsConfig?.[name] || {};
      const min = Number(configItem.min ?? -100);
      const max = Number(configItem.max ?? 100);
      const fallback = parseEdgeDefault(configItem.default, 0);
      const parsed = Number(value);
      if (!Number.isFinite(parsed)) {
        return fallback;
      }
      if (parsed < min) {
        return min;
      }
      if (parsed > max) {
        return max;
      }
      return Math.round(parsed);
    };

    const setEdgeInputValue = (input, name, value) => {
      if (!input) {
        return;
      }
      input.value = String(clampEdgeOption(name, value));
    };

    const getEngineOptionProfile = (engine) => {
      const key = String(engine || "");
      const profile = engineOptionsByEngine?.[key];
      if (profile && typeof profile === "object") {
        return profile;
      }
      return {};
    };

    const isProsodyEnabled = (name) => Boolean(edgeOptionsConfig?.[name]?.enabled);

    const getEdgeProsodyValues = () => ({
      edgeRate: isProsodyEnabled("rate") ? clampEdgeOption("rate", edgeRateInput?.value ?? 0) : 0,
      edgePitch: isProsodyEnabled("pitch") ? clampEdgeOption("pitch", edgePitchInput?.value ?? 0) : 0,
      edgeVolume: isProsodyEnabled("volume") ? clampEdgeOption("volume", edgeVolumeInput?.value ?? 0) : 0,
    });

    const supportsParallelism = (engine) => {
      const profile = getEngineOptionProfile(engine);
      return Boolean(profile.show_parallelism) || isEdgeEngine(engine);
    };

    const supportsGoogleRetry = (engine) => {
      const profile = getEngineOptionProfile(engine);
      if (Object.prototype.hasOwnProperty.call(profile, "show_retry_count")) {
        return Boolean(profile.show_retry_count);
      }
      return isGoogleEngine(engine);
    };

    const supportsLanguageSelector = (engine) => {
      const profile = getEngineOptionProfile(engine);
      const catalog = getEngineVoiceCatalog(engine);
      const hasCatalog = Object.keys(catalog || {}).length > 0;
      if (!hasCatalog) {
        return false;
      }
      if (Object.prototype.hasOwnProperty.call(profile, "show_language")) {
        return Boolean(profile.show_language);
      }
      return isEdgeEngine(engine);
    };

    const supportsTextNormalizer = (engine) => {
      const engineName = String(engine || "").trim();
      if (!engineName) {
        return false;
      }
      if (!toBool(edgeNormalizerConfig?.available, true)) {
        return false;
      }
      const profile = getEngineOptionProfile(engineName);
      if (Object.prototype.hasOwnProperty.call(profile, "show_text_normalizer")) {
        return toBool(profile.show_text_normalizer, true);
      }
      return true;
    };

    const applyEdgeOptionsConfig = (engine, options = null, resetValues = true) => {
      const profile = getEngineOptionProfile(engine);
      const source = options && typeof options === "object" ? options : profile;
      const mergeOption = (name, defaults) => ({
        ...defaults,
        ...((source && source[name]) || {}),
      });

      edgeOptionsConfig = {
        rate: mergeOption("rate", { enabled: true, min: -100, max: 100, default: 0, unit: "%" }),
        pitch: mergeOption("pitch", { enabled: true, min: -100, max: 100, default: 0, unit: "Hz" }),
        volume: mergeOption("volume", { enabled: true, min: -100, max: 100, default: 0, unit: "%" }),
      };

      if (edgeRateInput) {
        edgeRateInput.min = String(edgeOptionsConfig.rate.min ?? -100);
        edgeRateInput.max = String(edgeOptionsConfig.rate.max ?? 100);
      }
      if (secondaryRateInput) {
        secondaryRateInput.min = String(edgeOptionsConfig.rate.min ?? -100);
        secondaryRateInput.max = String(edgeOptionsConfig.rate.max ?? 100);
      }
      if (edgePitchInput) {
        edgePitchInput.min = String(edgeOptionsConfig.pitch.min ?? -100);
        edgePitchInput.max = String(edgeOptionsConfig.pitch.max ?? 100);
      }
      if (secondaryPitchInput) {
        secondaryPitchInput.min = String(edgeOptionsConfig.pitch.min ?? -100);
        secondaryPitchInput.max = String(edgeOptionsConfig.pitch.max ?? 100);
      }
      if (edgeVolumeInput) {
        edgeVolumeInput.min = String(edgeOptionsConfig.volume.min ?? -100);
        edgeVolumeInput.max = String(edgeOptionsConfig.volume.max ?? 100);
      }
      if (secondaryVolumeInput) {
        secondaryVolumeInput.min = String(edgeOptionsConfig.volume.min ?? -100);
        secondaryVolumeInput.max = String(edgeOptionsConfig.volume.max ?? 100);
      }

      if (edgeProsodyHint) {
        const parts = [];
        parts.push(
          `Скорость: ${edgeOptionsConfig.rate.min}..${edgeOptionsConfig.rate.max}${edgeOptionsConfig.rate.unit}${edgeOptionsConfig.rate.enabled ? "" : " (недоступно)"}.`
        );
        parts.push(
          `Тон: ${edgeOptionsConfig.pitch.min}..${edgeOptionsConfig.pitch.max}${edgeOptionsConfig.pitch.unit}${edgeOptionsConfig.pitch.enabled ? "" : " (недоступно)"}.`
        );
        parts.push(
          `Громкость: ${edgeOptionsConfig.volume.min}..${edgeOptionsConfig.volume.max}${edgeOptionsConfig.volume.unit}${edgeOptionsConfig.volume.enabled ? "" : " (недоступно)"}.`
        );
        if (profile.hint) {
          parts.push(String(profile.hint));
        }
        edgeProsodyHint.textContent = parts.join(" ");
      }

      if (resetValues) {
        setEdgeInputValue(edgeRateInput, "rate", parseEdgeDefault(edgeOptionsConfig.rate.default, 0));
        setEdgeInputValue(edgePitchInput, "pitch", parseEdgeDefault(edgeOptionsConfig.pitch.default, 0));
        setEdgeInputValue(edgeVolumeInput, "volume", parseEdgeDefault(edgeOptionsConfig.volume.default, 0));
        setEdgeInputValue(secondaryRateInput, "rate", parseEdgeDefault(edgeOptionsConfig.rate.default, 0));
        setEdgeInputValue(secondaryPitchInput, "pitch", parseEdgeDefault(edgeOptionsConfig.pitch.default, 0));
        setEdgeInputValue(secondaryVolumeInput, "volume", parseEdgeDefault(edgeOptionsConfig.volume.default, 0));
      } else {
        setEdgeInputValue(edgeRateInput, "rate", edgeRateInput?.value ?? edgeOptionsConfig.rate.default);
        setEdgeInputValue(edgePitchInput, "pitch", edgePitchInput?.value ?? edgeOptionsConfig.pitch.default);
        setEdgeInputValue(edgeVolumeInput, "volume", edgeVolumeInput?.value ?? edgeOptionsConfig.volume.default);
        setEdgeInputValue(secondaryRateInput, "rate", secondaryRateInput?.value ?? edgeOptionsConfig.rate.default);
        setEdgeInputValue(secondaryPitchInput, "pitch", secondaryPitchInput?.value ?? edgeOptionsConfig.pitch.default);
        setEdgeInputValue(secondaryVolumeInput, "volume", secondaryVolumeInput?.value ?? edgeOptionsConfig.volume.default);
      }
    };

    const updateEngineNote = (engine) => {
      if (!engineNote) {
        return;
      }
      const engineLimit = Object.prototype.hasOwnProperty.call(maxTextLenByEngine, engine)
        ? maxTextLenByEngine[engine]
        : uiMaxTextLen;
      const profile = getEngineOptionProfile(engine);

      if (engineLimit === null && isEdgeEngine(engine)) {
        const extra = profile.hint ? ` ${String(profile.hint)}` : "";
        engineNote.textContent = `Edge TTS: оптимальный режим для длинных текстов в текущей реализации, с автоматической сегментацией и склейкой аудио. Лимит поля ${uiMaxTextLen} символов.${extra}`;
        return;
      }

      const numericLimit = Number(engineLimit || uiMaxTextLen);
      const hint = profile.hint ? ` ${String(profile.hint)}` : "";
      engineNote.textContent = `Для движка ${engine || "по умолчанию"} максимум ${numericLimit} символов.${hint}`;
    };

    const applyTextLimitForEngine = (engine) => {
      const engineLimit = Object.prototype.hasOwnProperty.call(maxTextLenByEngine, engine)
        ? maxTextLenByEngine[engine]
        : uiMaxTextLen;
      if (engineLimit === null && isEdgeEngine(engine)) {
        maxTextLen = uiMaxTextLen;
        textArea.maxLength = uiMaxTextLen;
      } else {
        maxTextLen = Number(engineLimit || uiMaxTextLen);
        textArea.maxLength = maxTextLen;
      }
      updateEngineNote(engine);
    };

    const renderEdgeParallelismOptions = (selectedValue, engine = engineSelect.value) => {
      if (!edgeParallelismSelect) {
        return;
      }
      const activeConfig = getParallelismConfig(engine);
      const target = clampParallelism(
        selectedValue ?? activeConfig.current ?? activeConfig.default,
        engine
      );
      while (edgeParallelismSelect.options.length) {
        edgeParallelismSelect.remove(0);
      }
      for (let value = Number(activeConfig.min || 1); value <= Number(activeConfig.max || 12); value += 1) {
        const option = document.createElement("option");
        option.value = String(value);
        option.textContent = `${value}`;
        edgeParallelismSelect.appendChild(option);
      }
      edgeParallelismSelect.value = String(target);
      if (edgeParallelismHint) {
        if (isGoogleEngine(engine)) {
          edgeParallelismHint.textContent = `Диапазон ${activeConfig.min}-${activeConfig.max}. По умолчанию ${activeConfig.default}. Для gTTS обычно безопасно 1-4 потока (для длинных текстов лучше 1-2).`;
        } else {
          edgeParallelismHint.textContent = `Диапазон ${activeConfig.min}-${activeConfig.max}. По умолчанию ${activeConfig.default}. Обычно самый спокойный режим это 4-8.`;
        }
      }
    };

    const renderGoogleRetryOptions = (selectedValue) => {
      if (!googleRetrySelect) {
        return;
      }
      const target = clampGoogleRetryCount(
        selectedValue ?? googleRetryConfig.current ?? googleRetryConfig.default
      );
      while (googleRetrySelect.options.length) {
        googleRetrySelect.remove(0);
      }
      for (let value = Number(googleRetryConfig.min || 0); value <= Number(googleRetryConfig.max || 8); value += 1) {
        const option = document.createElement("option");
        option.value = String(value);
        option.textContent = `${value}`;
        googleRetrySelect.appendChild(option);
      }
      googleRetrySelect.value = String(target);
      if (googleRetryHint) {
        googleRetryHint.textContent = `Диапазон ${googleRetryConfig.min}-${googleRetryConfig.max}. По умолчанию ${googleRetryConfig.default}.`;
      }
    };

    const updateEdgeControlsForEngine = (engine) => {
      const languageEnabled = supportsLanguageSelector(engine);
      const parallelismEnabled = supportsParallelism(engine);
      const googleRetryEnabled = supportsGoogleRetry(engine);
      const activeParallelismConfig = getParallelismConfig(engine);

      if (edgeLanguageLabel) {
        edgeLanguageLabel.textContent = "Язык синтеза";
      }
      if (edgeParallelismLabel) {
        edgeParallelismLabel.textContent = isGoogleEngine(engine) ? "Потоки синтеза (gTTS)" : "Параллельность синтеза";
      }
      if (googleRetryLabel) {
        googleRetryLabel.textContent = "Повторы при ошибке (gTTS)";
      }
      if (edgeProsodyTitle) {
        edgeProsodyTitle.textContent = `Параметры звучания (${engine || "движок"})`;
      }

      if (edgeLanguageWrap) {
        edgeLanguageWrap.hidden = !languageEnabled;
      }
      if (edgeLanguageSelect) {
        edgeLanguageSelect.disabled = !languageEnabled || busy;
      }

      const dualSupported = supportsDualLanguage(engine);
      if (dualLanguageToggleWrap) {
        dualLanguageToggleWrap.hidden = !dualSupported;
      }
      if (dualLanguageEmptyHint) {
        dualLanguageEmptyHint.hidden = dualSupported;
      }
      if (dualLanguageEnabled) {
        if (!dualSupported) {
          dualLanguageEnabled.checked = false;
        }
        dualLanguageEnabled.disabled = !dualSupported || busy;
      }
      const dualActive = dualSupported && toBool(dualLanguageEnabled?.checked, false);
      if (dualLanguageWrap) {
        dualLanguageWrap.hidden = !dualActive;
      }
      if (secondaryLanguageSelect) {
        secondaryLanguageSelect.disabled = !dualActive || busy;
      }
      if (secondaryVoiceSelect) {
        secondaryVoiceSelect.disabled = !dualActive || busy;
      }
      if (secondaryProsodyWrap) {
        secondaryProsodyWrap.hidden = !dualActive;
      }
      if (secondaryRateInput) {
        secondaryRateInput.disabled = !dualActive || !isProsodyEnabled("rate") || busy;
      }
      if (secondaryPitchInput) {
        secondaryPitchInput.disabled = !dualActive || !isProsodyEnabled("pitch") || busy;
      }
      if (secondaryVolumeInput) {
        secondaryVolumeInput.disabled = !dualActive || !isProsodyEnabled("volume") || busy;
      }
      if (dualActive) {
        const selectedSecondaryLanguage = secondaryLanguageSelect?.value || "";
        const selectedSecondaryVoice = secondaryVoiceSelect?.value || "";
        const language = renderSecondaryLanguageOptions(
          selectedSecondaryLanguage,
          selectedSecondaryVoice,
          engine
        );
        renderSecondaryVoices(
          language,
          selectedSecondaryVoice,
          engine,
          voiceSelect?.value || ""
        );
      }
      if (secondaryLanguageHint) {
        secondaryLanguageHint.textContent = dualActive
          ? "Выберите язык и голос для фрагментов второго алфавита."
          : "Второй язык выключен.";
      }
      if (secondaryProsodyHint) {
        secondaryProsodyHint.textContent = dualActive
          ? "Настройки применяются только к второму голосу."
          : "Второй голос не используется.";
      }

      const dualPauseAvailable = toBool(dualPauseConfig.available, true);
      const dualPauseActive = dualActive && dualPauseAvailable;
      const dualPauseSettings = getDualPauseSettings();
      const dualPauseManual = dualPauseSettings.mode === "manual";
      if (dualPauseWrap) {
        dualPauseWrap.hidden = !dualPauseActive;
      }
      if (dualPauseModeSelect) {
        dualPauseModeSelect.disabled = !dualPauseActive || busy;
      }
      if (dualPauseMsWrap) {
        dualPauseMsWrap.hidden = !dualPauseActive || !dualPauseManual;
      }
      if (dualPauseMsInput) {
        dualPauseMsInput.disabled = !dualPauseActive || !dualPauseManual || busy;
      }
      if (dualPauseHint) {
        if (!dualPauseActive) {
          dualPauseHint.textContent = "Нормализация пауз доступна после включения второго языка.";
        } else if (dualPauseManual) {
          dualPauseHint.textContent = `Фиксированная пауза между сегментами: ${dualPauseSettings.pauseMs} мс.`;
        } else if (dualPauseSettings.mode === "off") {
          dualPauseHint.textContent = "Нормализация отключена: используется исходная пауза движка.";
        } else {
          dualPauseHint.textContent = String(
            dualPauseConfig?.auto?.hint
            || "Авто-режим уменьшает лишние паузы между фрагментами разных языков."
          );
        }
      }

      if (edgeParallelismWrap) {
        edgeParallelismWrap.hidden = !parallelismEnabled;
      }
      if (edgeParallelismSelect) {
        edgeParallelismSelect.disabled = !parallelismEnabled || busy;
      }
      if (edgeParallelismHint) {
        if (parallelismEnabled) {
          if (isGoogleEngine(engine)) {
            edgeParallelismHint.textContent = `Диапазон ${activeParallelismConfig.min}-${activeParallelismConfig.max}. По умолчанию ${activeParallelismConfig.default}. Для gTTS обычно безопасно 1-4 потока (для длинных текстов лучше 1-2).`;
          } else {
            edgeParallelismHint.textContent = `Диапазон ${activeParallelismConfig.min}-${activeParallelismConfig.max}. По умолчанию ${activeParallelismConfig.default}. Обычно самый спокойный режим это 4-8.`;
          }
        } else {
          edgeParallelismHint.textContent = "Для выбранного движка параллельный режим не используется.";
        }
      }

      if (googleRetryWrap) {
        googleRetryWrap.hidden = !googleRetryEnabled;
      }
      if (googleRetrySelect) {
        googleRetrySelect.disabled = !googleRetryEnabled || busy;
      }
      if (googleRetryHint) {
        googleRetryHint.textContent = googleRetryEnabled
          ? `Диапазон ${googleRetryConfig.min}-${googleRetryConfig.max}. По умолчанию ${googleRetryConfig.default}.`
          : "Для выбранного движка повторы gTTS не используются.";
      }

      if (edgeProsodyWrap) {
        edgeProsodyWrap.hidden = false;
      }
      applyEdgeOptionsConfig(engine, null, false);
      if (edgeRateInput) {
        edgeRateInput.disabled = !isProsodyEnabled("rate") || busy;
      }
      if (edgePitchInput) {
        edgePitchInput.disabled = !isProsodyEnabled("pitch") || busy;
      }
      if (edgeVolumeInput) {
        edgeVolumeInput.disabled = !isProsodyEnabled("volume") || busy;
      }

      const normalizerAllowed = supportsTextNormalizer(engine);
      if (edgeNormalizerWrap) {
        edgeNormalizerWrap.hidden = !normalizerAllowed;
      }
      if (edgeNormalizerEnabled) {
        edgeNormalizerEnabled.disabled = busy || !normalizerAllowed;
      }
      const normalizerDetailsDisabled = busy || !normalizerAllowed || !toBool(edgeNormalizerEnabled?.checked, true);
      [
        edgeNormalizerPreset,
        edgeNormalizerAuto,
        edgeNormalizerStripMarkdown,
        edgeNormalizerUnwrapMarkdownLinks,
        edgeNormalizerStripUrls,
        edgeNormalizerStripEmails,
        edgeNormalizerCollapseSymbols,
        edgeNormalizerCollapsePunctuation,
        edgeNormalizerPreserveEllipsis,
        edgeNormalizerDropSymbolTokens,
        edgeNormalizerWhitespace,
        edgeNormalizerCustomSymbolsInput,
      ].forEach((control) => {
        if (control) {
          control.disabled = normalizerDetailsDisabled;
        }
      });
      const analyzerAllowed = normalizerAllowed && Boolean(config.normalizePreviewUrl);
      if (edgeNormalizerAnalyzerWrap) {
        edgeNormalizerAnalyzerWrap.hidden = !analyzerAllowed;
      }
      if (edgeNormalizerAnalyzerAuto) {
        edgeNormalizerAnalyzerAuto.disabled = busy || !analyzerAllowed;
      }
      if (edgeNormalizerAnalyzeButton) {
        edgeNormalizerAnalyzeButton.disabled = busy || !analyzerAllowed;
      }
      if (edgeNormalizerSymbols) {
        edgeNormalizerSymbols.querySelectorAll("input[type='checkbox'][data-symbol], button[data-symbol-remove]").forEach((control) => {
          control.disabled = busy || !analyzerAllowed;
        });
      }
      if (edgeNormalizerHint && normalizerAllowed) {
        const autoTune = edgeNormalizerConfig?.auto_tune || {};
        const presetMap = getNormalizerPresetMap();
        const presetSelected = String(edgeNormalizerPreset?.value || "");
        const presetDescription = String(presetMap?.[presetSelected]?.description || "");
        const autoLabel = toBool(edgeNormalizerAuto?.checked, true)
          ? `Автонастройка: пороги ${Number(autoTune.balanced_threshold || 12000)} / ${Number(autoTune.aggressive_threshold || 60000)} символов.`
          : "Автонастройка отключена.";
        const baseHint = String(autoTune.hint || "");
        edgeNormalizerHint.textContent = `${autoLabel}${presetDescription ? ` ${presetDescription}` : ""}${baseHint ? ` ${baseHint}` : ""}`.trim();
      }
      if (!analyzerAllowed) {
        clearEdgeNormalizerAnalyzer("Анализатор недоступен.");
      } else if (!String(textArea.value || "").trim()) {
        clearEdgeNormalizerAnalyzer("Вставьте текст или импортируйте файл для запуска анализатора.");
      }
      updateEdgeNormalizerSummary();

      if (parallelismEnabled && edgeParallelismSelect && !edgeParallelismSelect.options.length) {
        renderEdgeParallelismOptions(undefined, engine);
      }
      if (googleRetryEnabled && googleRetrySelect && !googleRetrySelect.options.length) {
        renderGoogleRetryOptions();
      }
    };

    const clearSelect = (selectNode) => {
      while (selectNode.options.length) {
        selectNode.remove(0);
      }
    };

    const renderVoices = (engine, selectedVoice = "", selectedLanguage = "") => {
      if (supportsLanguageSelector(engine)) {
        const targetLanguage = renderEdgeLanguageOptions(selectedLanguage, selectedVoice, engine);
        const selected = renderEdgeVoices(targetLanguage, selectedVoice, engine);
        if (selected) {
          return;
        }
      }
      clearSelect(voiceSelect);
      const voices = Array.isArray(voicesByEngine[engine]) ? voicesByEngine[engine] : [];
      voices.forEach((voice) => {
        const option = document.createElement("option");
        option.value = voice;
        option.textContent = voice;
        voiceSelect.appendChild(option);
      });
      if (voices.length) {
        const target = selectedVoice && voices.includes(selectedVoice) ? selectedVoice : voices[0];
        voiceSelect.value = target;
      }
    };

    const applyEngineState = (
      engine,
      selectedVoice = "",
      selectedParallelism = null,
      edgeOptions = null,
      selectedLanguage = "",
      selectedGoogleRetryCount = null,
      selectedSecondaryLanguage = "",
      selectedSecondaryVoice = "",
      secondaryEdgeOptions = null,
      selectedDualEnabled = null,
      selectedDualPauseSettings = null
    ) => {
      renderVoices(engine, selectedVoice, selectedLanguage);
      applyTextLimitForEngine(engine);
      applyEdgeOptionsConfig(engine, null, true);
      if (dualLanguageEnabled && selectedDualEnabled !== null && selectedDualEnabled !== undefined) {
        dualLanguageEnabled.checked = toBool(selectedDualEnabled, false);
      }
      updateEdgeControlsForEngine(engine);
      if (supportsParallelism(engine)) {
        renderEdgeParallelismOptions(selectedParallelism, engine);
      }
      if (supportsGoogleRetry(engine)) {
        renderGoogleRetryOptions(selectedGoogleRetryCount);
      }
      if (edgeOptions && typeof edgeOptions === "object") {
        setEdgeInputValue(edgeRateInput, "rate", edgeOptions.edgeRate ?? edgeOptions.rate ?? 0);
        setEdgeInputValue(edgePitchInput, "pitch", edgeOptions.edgePitch ?? edgeOptions.pitch ?? 0);
        setEdgeInputValue(edgeVolumeInput, "volume", edgeOptions.edgeVolume ?? edgeOptions.volume ?? 0);
      }
      if (isDualLanguageEnabled(engine)) {
        const targetSecondaryLanguage = renderSecondaryLanguageOptions(
          selectedSecondaryLanguage,
          selectedSecondaryVoice,
          engine
        );
        renderSecondaryVoices(
          targetSecondaryLanguage,
          selectedSecondaryVoice,
          engine,
          voiceSelect?.value || ""
        );
      }
      if (secondaryEdgeOptions && typeof secondaryEdgeOptions === "object") {
        setEdgeInputValue(secondaryRateInput, "rate", secondaryEdgeOptions.edgeRate ?? secondaryEdgeOptions.rate ?? 0);
        setEdgeInputValue(secondaryPitchInput, "pitch", secondaryEdgeOptions.edgePitch ?? secondaryEdgeOptions.pitch ?? 0);
        setEdgeInputValue(secondaryVolumeInput, "volume", secondaryEdgeOptions.edgeVolume ?? secondaryEdgeOptions.volume ?? 0);
      }
      if (selectedDualPauseSettings && typeof selectedDualPauseSettings === "object") {
        setDualPauseSettings(selectedDualPauseSettings);
      }
      if (isEdgeNormalizerAnalyzerAvailable(engine)) {
        queueEdgeNormalizerAnalysis({ delayMs: 180 });
      }
    };

    const renderEngines = (
      engines,
      voices,
      selectedEngine = "",
      selectedParallelism = null,
      selectedEdgeLanguage = "",
      selectedGoogleRetryCount = null
    ) => {
      const availableEngines = Array.isArray(engines)
        ? engines.map((item) => String(item || "").trim()).filter(Boolean)
        : [];
      voicesByEngine = voices || {};
      clearSelect(engineSelect);
      availableEngines.forEach((engine) => {
        const option = document.createElement("option");
        option.value = engine;
        option.textContent = engine;
        engineSelect.appendChild(option);
      });
      if (engineSelect.options.length) {
        const preferredEngine = getPreferredEngine(availableEngines, engineSelect.options[0].value);
        const target =
          selectedEngine && availableEngines.includes(selectedEngine)
            ? selectedEngine
            : preferredEngine;
        engineSelect.value = target;
        applyEngineState(target, "", selectedParallelism, null, selectedEdgeLanguage, selectedGoogleRetryCount);
      }
    };

    const renderHistory = () => {
      const items = loadHistory();
      historyBox.innerHTML = "";
      if (!historyAvailable) {
        const empty = document.createElement("div");
        empty.className = "wintts-history-empty";
        empty.textContent = messages.historyUnavailable;
        historyBox.appendChild(empty);
        if (historyClearButton) {
          historyClearButton.disabled = true;
        }
        return;
      }
      if (!items.length) {
        const empty = document.createElement("div");
        empty.className = "wintts-history-empty";
        empty.textContent = messages.historyEmpty;
        historyBox.appendChild(empty);
        if (historyClearButton) {
          historyClearButton.disabled = true;
        }
        return;
      }

      items.forEach((item) => {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "wintts-history-item";
        btn.setAttribute("role", "listitem");
        const ts = item.ts ? new Date(item.ts * 1000).toLocaleString() : "";
        const parallelLabel = item.edgeParallelism && supportsParallelism(item.engine)
          ? ` | x${item.edgeParallelism}`
          : "";
        const retryLabel = supportsGoogleRetry(item.engine) && item.googleRetryCount !== null && item.googleRetryCount !== undefined
          ? ` | retry${Number(item.googleRetryCount)}`
          : "";
        const edgeRate = Number(item.edgeRate || 0);
        const edgePitch = Number(item.edgePitch || 0);
        const edgeVolume = Number(item.edgeVolume || 0);
        const edgeOptsLabel = (edgeRate || edgePitch || edgeVolume)
          ? ` | r${edgeRate}% p${edgePitch}Hz v${edgeVolume}%`
          : "";
        const normalizerLabel = (supportsTextNormalizer(item.engine) && item.edgeTextNormalizer)
          ? ` | nrm:${item.edgeTextNormalizer.preset || "balanced"}${item.edgeTextNormalizer.enabled ? "" : "(выкл.)"}`
          : "";
        const dualLabel = toBool(item.dualLanguageEnabled, false)
          ? ` | dual:${item.secondaryLanguage || "?"}:${item.secondaryVoice || "?"}`
          : "";
        const dualPauseMode = normalizeDualPauseMode(item.dualPauseMode || "auto");
        const dualPauseLabel = toBool(item.dualLanguageEnabled, false)
          ? (dualPauseMode === "manual"
            ? ` | pause:${dualPauseMode}:${Number(item.dualPauseMs || 0)}ms`
            : ` | pause:${dualPauseMode}`)
          : "";
        const title = `${item.engine || ""} | ${item.voice || ""}${parallelLabel}${retryLabel}${edgeOptsLabel}${normalizerLabel}${dualLabel}${dualPauseLabel}`;
        const historyText = String(item.text || item.textSnippet || "");
        const shortText = historyText.slice(0, 110);
        btn.textContent = `${title} :: ${shortText}${shortText.length >= 110 ? "..." : ""}`;
        btn.title = ts ? `${ts}\n${historyText}` : historyText;
        btn.addEventListener("click", () => {
          if (item.engine) {
            engineSelect.value = item.engine;
            applyEngineState(
              item.engine,
              item.voice || "",
              item.googleParallelism ?? item.edgeParallelism ?? null,
              {
                edgeRate: item.edgeRate,
                edgePitch: item.edgePitch,
                edgeVolume: item.edgeVolume,
              },
              item.edgeLanguage || "",
              item.googleRetryCount ?? null,
              item.secondaryLanguage || "",
              item.secondaryVoice || "",
              {
                edgeRate: item.secondaryEdgeRate,
                edgePitch: item.secondaryEdgePitch,
                edgeVolume: item.secondaryEdgeVolume,
              },
              toBool(item.dualLanguageEnabled, false),
              {
                mode: item.dualPauseMode,
                pauseMs: item.dualPauseMs,
              }
            );
            if (supportsTextNormalizer(item.engine) && item.edgeTextNormalizer) {
              const normalizedItemSettings = normalizeEdgeNormalizerSettings(
                item.edgeTextNormalizer,
                edgeNormalizerConfig?.default || edgeNormalizerDefaultSettings
              );
              setEdgeNormalizerSettings(normalizedItemSettings, true);
              if (edgeNormalizerPreset) {
                const candidatePreset = String(normalizedItemSettings.preset || "");
                const hasCandidate = Array.from(edgeNormalizerPreset.options).some((opt) => opt.value === candidatePreset);
                edgeNormalizerPreset.value = hasCandidate ? candidatePreset : "__custom__";
                if (hasCandidate && candidatePreset) {
                  edgeNormalizerLastPreset = candidatePreset;
                }
              }
              updateEdgeNormalizerSummary();
            }
          }
          textArea.value = historyText;
          queueEdgeNormalizerAnalysis({ delayMs: 120 });
          if (item.audioUrl && item.downloadUrl) {
            setAudioResult(item.audioUrl, item.downloadUrl, item.filename || "");
          }
          textArea.focus();
        });
        historyBox.appendChild(btn);
      });

      if (historyClearButton) {
        historyClearButton.disabled = false;
      }
    };

    const addHistory = (item) => {
      const current = loadHistory();
      current.unshift(item);
      const limited = current.slice(0, historyLimit);
      saveHistory(limited);
      renderHistory();
    };

    const setAudioResult = (audioUrl, downloadUrl, filename) => {
      const stamp = `t=${Date.now()}`;
      const audioFinalUrl = audioUrl.includes("?")
        ? `${audioUrl}&${stamp}`
        : `${audioUrl}?${stamp}`;
      audioPlayer.src = audioFinalUrl;
      audioPlayer.load();
      audioSection.hidden = false;
      downloadLink.href = downloadUrl;
      if (filename) {
        downloadLink.setAttribute("download", filename);
      }
    };

    const loadConfig = async () => {
      setOutput(messages.configLoading, true);
      if (!config.configUrl) {
        setOutput(messages.configFail, false);
        renderDiagnostics([], true);
        return;
      }
      try {
        const response = await fetch(config.configUrl, {
          method: "GET",
          credentials: "same-origin",
          headers: { Accept: "application/json" },
        });
        const { data, rawText, parseError } = await parseJsonResponse(response);
        if (!data || typeof data !== "object") {
          setOutput(messages.configFail, false);
          renderHttpFallbackDiagnostics(response, parseError, rawText);
          return;
        }

        if (!response.ok || !data || !data.ok) {
          const errorText = (data && (data.error || data.message)) || messages.configFail;
          setOutput(errorText, false);
          const lines = toLines(data?.diagnostics_lines);
          if (lines.length) {
            renderDiagnostics(lines, true);
          } else {
            renderHttpFallbackDiagnostics(response, "", rawText);
          }
          return;
        }

        uiMaxTextLen = Number(data.max_text_len || 5000);
        maxTextLenByEngine = data.max_text_len_by_engine || {};
        maxUploadBytes = Number(data.max_upload_bytes || 0);
        edgeParallelismConfig = {
          min: Number(data?.edge_parallelism?.min || 1),
          max: Number(data?.edge_parallelism?.max || 12),
          default: Number(data?.edge_parallelism?.default || 4),
          current: Number(data?.edge_parallelism?.current || data?.edge_parallelism?.default || 4),
        };
        googleParallelismConfig = {
          min: Number(data?.google_parallelism?.min || 1),
          max: Number(data?.google_parallelism?.max || 12),
          default: Number(data?.google_parallelism?.default || 4),
          current: Number(data?.google_parallelism?.current || data?.google_parallelism?.default || 4),
        };
        googleRetryConfig = {
          min: Number(data?.google_retry_count?.min ?? 0),
          max: Number(data?.google_retry_count?.max ?? 8),
          default: Number(data?.google_retry_count?.default ?? 2),
          current: Number(data?.google_retry_count?.current ?? data?.google_retry_count?.default ?? 2),
        };
        engineOptionsByEngine = data?.engine_options && typeof data.engine_options === "object"
          ? data.engine_options
          : {};
        if (!Object.keys(engineOptionsByEngine).length) {
          engineOptionsByEngine = {
            Google: {
              show_language: true,
              show_parallelism: true,
              show_retry_count: true,
              show_text_normalizer: true,
            },
            "Edge TTS": {
              show_language: true,
              show_parallelism: true,
              show_retry_count: false,
              show_text_normalizer: true,
              ...(data?.edge_options || {}),
            },
          };
        }
        applyDualPauseConfig(data?.dual_pause || {});
        applyEdgeNormalizerConfig(data?.text_normalizer || data?.edge_text_normalizer || {}, true);

        const voiceCatalogPayload = data?.voice_catalog && typeof data.voice_catalog === "object"
          ? data.voice_catalog
          : { "Edge TTS": data?.edge_voice_catalog || {} };
        setEdgeVoiceCatalog(voiceCatalogPayload, data?.voices || {});

        const targetEngine = engineSelect.value || String(data.default_engine || "");
        renderEngines(
          data.engines || [],
          data.voices || {},
          targetEngine,
          edgeParallelismConfig.current,
          edgeLanguageSelect?.value || "",
          googleRetryConfig.current
        );
        renderEdgeParallelismOptions(undefined, engineSelect.value);
        renderGoogleRetryOptions(googleRetryConfig.current);
        applyTextLimitForEngine(engineSelect.value);
        updateEdgeControlsForEngine(engineSelect.value);

        if (installButton) {
          installButton.hidden = !Boolean(data.can_install);
        }
        const addonRuntime = data?.addon_runtime && typeof data.addon_runtime === "object"
          ? data.addon_runtime
          : null;
        if (addonRuntime) {
          const addonInstalled = toBool(addonRuntime.installed, false);
          const addonBroken = toBool(addonRuntime.broken, false);
          const addonCanInstall = toBool(addonRuntime.can_install, false);
          if (addonInstalled) {
            setInstallLogStatus("RHVoice-addon установлен и готов к работе.", "ok");
          } else if (addonBroken) {
            setInstallLogStatus("RHVoice-addon поврежден. Рекомендуется переустановка.", "error");
          } else if (addonCanInstall) {
            setInstallLogStatus(messages.installLogIdle, "info");
          } else {
            setInstallLogStatus("RHVoice-addon недоступен: нет Python или прав записи.", "error");
          }

          const existingLogLines = toLines(installLogOutput?.textContent || "");
          const hasDefaultText = existingLogLines.length <= 1 && /лог установки/i.test(existingLogLines.join(" "));
          if (hasDefaultText) {
            const runtimeLines = [
              `Статус addon: ${String(addonRuntime.status || "unknown")}`,
              `Папка addon: ${String(addonRuntime.addon_root || "-")}`,
              `Python: ${String(addonRuntime.base_python || "-")}`,
            ];
            renderInstallLog(runtimeLines, { append: false, open: false });
          }
        }
        if (fileHint) {
          const formats = Array.isArray(data.supported_import_extensions) ? data.supported_import_extensions.join(", ") : ".txt, .fb2, .epub, .docx, .md, .html";
          const sizeLabel = maxUploadBytes > 0 ? formatBytes(maxUploadBytes) : "не задан";
          fileHint.textContent = `Поддерживаются ${formats}. Максимальный размер файла: ${sizeLabel}. Файл можно загрузить в текстовое поле ниже, при необходимости поправить и затем озвучить.`;
        }
        updateFileInfo();

        const warnings = Array.isArray(data.warnings) ? data.warnings.filter(Boolean) : [];
        if (warnings.length) {
          setOutput(`Доступны не все TTS-движки: ${warnings.join(" | ")}`, false);
        } else {
          setOutput("Плагин готов к работе.", true);
        }

        renderDiagnostics(data.diagnostics_lines || [], warnings.length > 0);
      } catch (err) {
        setOutput(messages.configFail, false);
        const message =
          err && err.message
            ? `Ошибка запроса конфигурации: ${String(err.message)}`
            : "Ошибка запроса конфигурации.";
        renderDiagnostics([message], true);
      }
    };

    const runSynthesisLegacy = async (requestMeta) => {
      try {
        const response = await fetch(config.synthUrl, {
          method: "POST",
          credentials: "same-origin",
          headers: {
            "Content-Type": "application/json",
            "X-CSRFToken": config.csrfToken,
            Accept: "application/json",
          },
          body: JSON.stringify({
            text: requestMeta.text,
            engine: requestMeta.engine,
            voice: requestMeta.voice,
            edge_parallelism: requestMeta.edgeParallelism,
            google_parallelism: requestMeta.googleParallelism,
            google_retry_count: requestMeta.googleRetryCount,
            primary_language: requestMeta.edgeLanguage,
            edge_language: requestMeta.edgeLanguage,
            edge_rate: requestMeta.edgeRate,
            edge_pitch: requestMeta.edgePitch,
            edge_volume: requestMeta.edgeVolume,
            dual_language: requestMeta.dualLanguageEnabled
              ? {
                enabled: true,
                secondary_language: requestMeta.secondaryLanguage,
                secondary_voice: requestMeta.secondaryVoice,
                secondary_edge_rate: requestMeta.secondaryEdgeRate,
                secondary_edge_pitch: requestMeta.secondaryEdgePitch,
                secondary_edge_volume: requestMeta.secondaryEdgeVolume,
                pause_mode: requestMeta.dualPauseMode,
                pause_ms: requestMeta.dualPauseMs,
              }
              : null,
            edge_text_normalizer: requestMeta.edgeTextNormalizer,
            text_normalizer: requestMeta.edgeTextNormalizer,
            csrf_token: config.csrfToken,
          }),
        });

        const { data, rawText, parseError } = await parseJsonResponse(response);

        if (!data) {
          setOutput(messages.synthFail, false);
          renderHttpFallbackDiagnostics(response, parseError, rawText);
          return;
        }

        if (!response.ok || !data.ok) {
          setOutput(data.error || data.message || messages.synthFail, false);
          const lines = toLines(data.diagnostics_lines);
          if (lines.length) {
            renderDiagnostics(lines, true);
          } else if (!response.ok) {
            renderHttpFallbackDiagnostics(response, "", rawText);
          } else {
            renderDiagnostics([data.error || data.message || messages.synthFail], true);
          }
          return;
        }

        setOutput(data.message || messages.synthDone, true);
        renderLiveLog({
          status: "done",
          percent: 100,
          message: data.message || messages.synthDone,
          logs: [
            {
              ts: Math.floor(Date.now() / 1000),
              percent: 100,
              level: "info",
              message: data.message || messages.synthDone,
            },
          ],
        });
        if (data.audio_url && data.download_url) {
          setAudioResult(data.audio_url, data.download_url, data.filename || "");
          addHistory(buildHistoryItem(requestMeta, data));
        }
        const normalizerSummary = data?.text_normalizer_result?.summary || data?.edge_text_normalizer_result?.summary;
        if (normalizerSummary) {
          updateEdgeNormalizerSummary(`Последний синтез: ${String(normalizerSummary)}`);
        } else {
          updateEdgeNormalizerSummary();
        }
        renderDiagnostics(data.diagnostics_lines || [], false);
      } catch (err) {
        setOutput(messages.sendFail, false);
        const message =
          err && err.message
            ? `Ошибка сети при синтезе: ${String(err.message)}`
            : "Ошибка сети при синтезе.";
        appendLiveLogLine(message, "error");
        renderDiagnostics([message], true);
      } finally {
        setBusy(false);
        updateEdgeControlsForEngine(engineSelect.value);
      }
    };

    const pollSynthesisStatus = async (jobId, requestMeta) => {
      if (!jobId || activeSynthesisJobId !== jobId) {
        return;
      }

      const statusUrl = config.synthStatusUrlTemplate.replace("__JOB_ID__", encodeURIComponent(jobId));
      try {
        const response = await fetch(statusUrl, {
          method: "GET",
          credentials: "same-origin",
          cache: "no-store",
          headers: { Accept: "application/json" },
        });
        const { data, rawText, parseError } = await parseJsonResponse(response);

        if (!data) {
          stopLiveLogPolling();
          setBusy(false);
          updateEdgeControlsForEngine(engineSelect.value);
          setOutput(messages.synthFail, false);
          appendLiveLogLine(messages.liveLogNetworkError, "error");
          renderHttpFallbackDiagnostics(response, parseError, rawText);
          return;
        }

        renderLiveLog(data);

        if (!response.ok) {
          stopLiveLogPolling();
          setBusy(false);
          updateEdgeControlsForEngine(engineSelect.value);
          setOutput(data.error || data.message || messages.synthFail, false);
          const lines = toLines(data.diagnostics_lines);
          if (lines.length) {
            renderDiagnostics(lines, true);
          } else {
            renderHttpFallbackDiagnostics(response, "", rawText);
          }
          return;
        }

        if (data.done || data.status === "done" || data.status === "error") {
          stopLiveLogPolling();
          setBusy(false);
          updateEdgeControlsForEngine(engineSelect.value);

          if (!data.ok || data.status === "error") {
            setOutput(data.error || data.message || messages.synthFail, false);
            const lines = toLines(data.diagnostics_lines);
            renderDiagnostics(lines.length ? lines : [data.error || data.message || messages.synthFail], true);
            return;
          }

          setOutput(data.message || messages.synthDone, true);
          if (data.audio_url && data.download_url) {
            setAudioResult(data.audio_url, data.download_url, data.filename || "");
            addHistory(buildHistoryItem(requestMeta, data));
          }
          const normalizerSummary = data?.text_normalizer_result?.summary || data?.edge_text_normalizer_result?.summary;
          if (normalizerSummary) {
            updateEdgeNormalizerSummary(`Последний синтез: ${String(normalizerSummary)}`);
          } else {
            updateEdgeNormalizerSummary();
          }
          renderDiagnostics(data.diagnostics_lines || [], false);
          return;
        }

        setOutput(data.message || messages.synthRunning, true);
        queueLiveLogPoll(jobId, getNextPollDelay(String(data.status || "running"), clampPercent(data.percent)));
      } catch (err) {
        stopLiveLogPolling();
        setBusy(false);
        updateEdgeControlsForEngine(engineSelect.value);
        const message =
          err && err.message
            ? `Ошибка сети при получении лога: ${String(err.message)}`
            : messages.liveLogNetworkError;
        appendLiveLogLine(message, "error");
        setOutput(message, false);
        renderDiagnostics([message], true);
      }
    };

    const runSynthesis = async () => {
      if (busy) {
        return;
      }

      if (!config.csrfToken) {
        setOutput(messages.csrfMissing, false);
        return;
      }

      const text = await ensureTextAvailable();
      if (!text) {
        setOutput(messages.textEmpty, false);
        return;
      }
      if (text.length > maxTextLen) {
        setOutput(`Текст слишком длинный. Максимум ${maxTextLen} символов.`, false);
        return;
      }

      const requestMeta = getRequestMeta(text);
      if (requestMeta.dualLanguageEnabled && !requestMeta.secondaryVoice) {
        setOutput("Для второго языка выберите второй голос.", false);
        return;
      }
      if (
        requestMeta.dualLanguageEnabled
        && requestMeta.secondaryVoice
        && requestMeta.voice
        && requestMeta.secondaryVoice === requestMeta.voice
      ) {
        setOutput("Первый и второй голос должны отличаться.", false);
        return;
      }
      if (
        requestMeta.dualLanguageEnabled
        && requestMeta.secondaryLanguage
        && requestMeta.edgeLanguage
        && requestMeta.secondaryLanguage === requestMeta.edgeLanguage
      ) {
        setOutput("Первый и второй язык должны отличаться.", false);
        return;
      }

      if (!config.synthStartUrl || !config.synthStatusUrlTemplate) {
        if (!config.synthUrl) {
          setOutput(messages.synthFail, false);
          return;
        }
        setBusy(true);
        setOutput(messages.synthRunning, true);
        renderLiveLog({
          status: "running",
          percent: 10,
          message: messages.synthRunning,
          logs: [
            {
              ts: Math.floor(Date.now() / 1000),
              percent: 10,
              level: "info",
              message: "Сервер не поддерживает потоковый лог. Использую обычный режим синтеза.",
            },
          ],
        });
        await runSynthesisLegacy(requestMeta);
        return;
      }

      setBusy(true);
      stopLiveLogPolling();
      renderLiveLog({
        status: "queued",
        percent: 0,
        message: messages.liveLogQueued,
        logs: [
          {
            ts: Math.floor(Date.now() / 1000),
            percent: 0,
            level: "info",
            message: "Отправляю запрос на запуск синтеза.",
          },
        ],
      });
      setOutput(messages.synthRunning, true);

      try {
        const response = await fetch(config.synthStartUrl, {
          method: "POST",
          credentials: "same-origin",
          headers: {
            "Content-Type": "application/json",
            "X-CSRFToken": config.csrfToken,
            Accept: "application/json",
          },
          body: JSON.stringify({
            text: requestMeta.text,
            engine: requestMeta.engine,
            voice: requestMeta.voice,
            edge_parallelism: requestMeta.edgeParallelism,
            google_parallelism: requestMeta.googleParallelism,
            google_retry_count: requestMeta.googleRetryCount,
            primary_language: requestMeta.edgeLanguage,
            edge_language: requestMeta.edgeLanguage,
            edge_rate: requestMeta.edgeRate,
            edge_pitch: requestMeta.edgePitch,
            edge_volume: requestMeta.edgeVolume,
            dual_language: requestMeta.dualLanguageEnabled
              ? {
                enabled: true,
                secondary_language: requestMeta.secondaryLanguage,
                secondary_voice: requestMeta.secondaryVoice,
                secondary_edge_rate: requestMeta.secondaryEdgeRate,
                secondary_edge_pitch: requestMeta.secondaryEdgePitch,
                secondary_edge_volume: requestMeta.secondaryEdgeVolume,
                pause_mode: requestMeta.dualPauseMode,
                pause_ms: requestMeta.dualPauseMs,
              }
              : null,
            edge_text_normalizer: requestMeta.edgeTextNormalizer,
            text_normalizer: requestMeta.edgeTextNormalizer,
            csrf_token: config.csrfToken,
          }),
        });

        const { data, rawText, parseError } = await parseJsonResponse(response);
        if (!data) {
          setBusy(false);
          updateEdgeControlsForEngine(engineSelect.value);
          setOutput(messages.synthFail, false);
          appendLiveLogLine(messages.liveLogNetworkError, "error");
          renderHttpFallbackDiagnostics(response, parseError, rawText);
          return;
        }

        if (!response.ok || !data.ok || !data.job_id) {
          setBusy(false);
          updateEdgeControlsForEngine(engineSelect.value);
          setOutput(data.error || data.message || messages.synthFail, false);
          renderLiveLog({
            status: "error",
            percent: clampPercent(data.percent),
            message: data.error || data.message || messages.synthFail,
            logs: Array.isArray(data.logs) ? data.logs : [],
          });
          const lines = toLines(data.diagnostics_lines);
          if (lines.length) {
            renderDiagnostics(lines, true);
          } else if (!response.ok) {
            renderHttpFallbackDiagnostics(response, "", rawText);
          } else {
            renderDiagnostics([data.error || data.message || messages.synthFail], true);
          }
          return;
        }

        activeSynthesisJobId = String(data.job_id);
        activeSynthesisRequest = requestMeta;
        renderLiveLog(data);
        await pollSynthesisStatus(activeSynthesisJobId, requestMeta);
      } catch (err) {
        stopLiveLogPolling();
        setBusy(false);
        updateEdgeControlsForEngine(engineSelect.value);
        setOutput(messages.sendFail, false);
        const message =
          err && err.message
            ? `Ошибка сети при запуске синтеза: ${String(err.message)}`
            : "Ошибка сети при запуске синтеза.";
        appendLiveLogLine(message, "error");
        renderDiagnostics([message], true);
      }
    };

    const installDependencies = async () => {
      if (busy || !config.installUrl) {
        return;
      }
      if (!config.csrfToken) {
        setOutput(messages.csrfMissing, false);
        setInstallLogStatus(messages.csrfMissing, "error");
        return;
      }

      setBusy(true);
      setOutput(messages.installRunning, true);
      setInstallLogStatus(messages.installLogStarted, "info");
      renderInstallLog(
        [
          `[${new Date().toLocaleTimeString("ru-RU")}] Запуск установки RHVoice-addon.`,
          "Подготавливаю запрос к серверу и проверяю доступность сети.",
        ],
        { append: false, open: true }
      );

      try {
        const response = await fetch(config.installUrl, {
          method: "POST",
          credentials: "same-origin",
          headers: {
            "Content-Type": "application/json",
            "X-CSRFToken": config.csrfToken,
            Accept: "application/json",
          },
          body: JSON.stringify({ csrf_token: config.csrfToken }),
        });

        const { data, rawText, parseError } = await parseJsonResponse(response);

        if (!data) {
          setOutput(messages.installFail, false);
          setInstallLogStatus(messages.installLogError, "error");
          renderInstallLog(
            [
              `[${new Date().toLocaleTimeString("ru-RU")}] Сервер вернул невалидный ответ.`,
              `HTTP статус: ${response.status} ${response.statusText || ""}`.trim(),
              parseError ? `Ошибка разбора JSON: ${parseError}` : "",
              toBodySnippet(rawText),
            ],
            { append: true, open: true }
          );
          renderHttpFallbackDiagnostics(response, parseError, rawText);
          return;
        }

        const detailLines = toLines(data.details);
        const diagnosticLines = toLines(data.diagnostics_lines);
        const installLogLines = [
          `[${new Date().toLocaleTimeString("ru-RU")}] ${String(data.message || "")}`.trim(),
          ...detailLines,
        ].filter(Boolean);
        if (!detailLines.length && diagnosticLines.length) {
          installLogLines.push(...diagnosticLines.slice(0, 30));
        }
        if (installLogLines.length) {
          renderInstallLog(installLogLines, { append: true, open: true });
        }

        if (!response.ok || !data.ok) {
          setOutput(data.error || data.message || messages.installFail, false);
          setInstallLogStatus(data.error || data.message || messages.installLogError, "error");
        } else {
          setOutput(data.message || messages.installDone, true);
          setInstallLogStatus(data.message || messages.installLogDone, "ok");
        }

        const lines = toLines(data.diagnostics_lines);
        if (lines.length) {
          renderDiagnostics(lines, true);
        } else if (!response.ok || !data.ok) {
          renderHttpFallbackDiagnostics(response, "", rawText);
        } else {
          renderDiagnostics([], false);
        }
        await loadConfig();
      } catch (err) {
        setOutput(messages.installFail, false);
        const message =
          err && err.message
            ? `Ошибка сети при установке RHVoice-addon: ${String(err.message)}`
            : "Ошибка сети при установке RHVoice-addon.";
        setInstallLogStatus(message, "error");
        renderInstallLog(
          [
            `[${new Date().toLocaleTimeString("ru-RU")}] ${message}`,
            "Проверьте интернет, прокси/VPN, файрвол, антивирус и доступ к pypi.org.",
          ],
          { append: true, open: true }
        );
        renderDiagnostics([message], true);
      } finally {
        setBusy(false);
        updateEdgeControlsForEngine(engineSelect.value);
      }
    };

    runButton.addEventListener("click", runSynthesis);

    clearButton.addEventListener("click", () => {
      textArea.value = "";
      if (!fileInput?.files || !fileInput.files.length) {
        resetSourceMeta();
      }
      updateFileInfo();
      clearEdgeNormalizerAnalyzer("Вставьте текст или импортируйте файл для запуска анализатора.");
      textArea.focus();
    });

    importFileButton?.addEventListener("click", async () => {
      if (busy) {
        return;
      }
      try {
        setBusy(true);
        await importSelectedFile({ announce: true });
      } catch (err) {
        const message = err && err.message ? `Ошибка импорта файла: ${String(err.message)}` : "Ошибка импорта файла.";
        setOutput(message, false);
        updateFileInfo(message, true);
        renderDiagnostics([message], true);
      } finally {
        setBusy(false);
        updateEdgeControlsForEngine(engineSelect.value);
        queueEdgeNormalizerAnalysis({ delayMs: 80 });
      }
    });

    clearFileButton?.addEventListener("click", () => {
      if (fileInput) {
        fileInput.value = "";
      }
      resetSourceMeta();
      updateFileInfo();
      textArea.focus();
    });

    fileInput?.addEventListener("change", () => {
      resetSourceMeta();
      updateFileInfo();
    });

    installButton?.addEventListener("click", installDependencies);

    engineSelect.addEventListener("change", () => {
      applyEngineState(
        engineSelect.value,
        voiceSelect.value,
        getEdgeParallelismValue(engineSelect.value),
        null,
        edgeLanguageSelect?.value || "",
        getGoogleRetryCountValue(),
        secondaryLanguageSelect?.value || "",
        secondaryVoiceSelect?.value || "",
        null,
        toBool(dualLanguageEnabled?.checked, false)
      );
    });

    edgeLanguageSelect?.addEventListener("change", () => {
      const engine = engineSelect.value;
      if (!supportsLanguageSelector(engine)) {
        return;
      }
      const selectedLanguage = getEdgeLanguageValue(engine);
      renderEdgeVoices(selectedLanguage, voiceSelect.value, engine);
      if (isDualLanguageEnabled(engine)) {
        const secondaryLanguage = renderSecondaryLanguageOptions(
          secondaryLanguageSelect?.value || "",
          secondaryVoiceSelect?.value || "",
          engine
        );
        renderSecondaryVoices(
          secondaryLanguage,
          secondaryVoiceSelect?.value || "",
          engine,
          voiceSelect.value
        );
      }
    });

    voiceSelect.addEventListener("change", () => {
      const engine = engineSelect.value;
      if (!supportsLanguageSelector(engine) || !edgeLanguageSelect) {
        return;
      }
      const language = getEdgeVoiceLanguage(voiceSelect.value, engine);
      const catalog = getEngineVoiceCatalog(engine);
      if (language && catalog[language] && edgeLanguageSelect.value !== language) {
        edgeLanguageSelect.value = language;
      }
      if (isDualLanguageEnabled(engine)) {
        const secondaryLanguage = renderSecondaryLanguageOptions(
          secondaryLanguageSelect?.value || "",
          secondaryVoiceSelect?.value || "",
          engine
        );
        renderSecondaryVoices(
          secondaryLanguage,
          secondaryVoiceSelect?.value || "",
          engine,
          voiceSelect.value
        );
      }
    });

    dualLanguageEnabled?.addEventListener("change", () => {
      updateEdgeControlsForEngine(engineSelect.value);
    });

    secondaryLanguageSelect?.addEventListener("change", () => {
      const engine = engineSelect.value;
      if (!isDualLanguageEnabled(engine)) {
        return;
      }
      const selectedLanguage = getSecondaryLanguageValue(engine);
      renderSecondaryVoices(
        selectedLanguage,
        secondaryVoiceSelect?.value || "",
        engine,
        voiceSelect.value
      );
    });

    secondaryVoiceSelect?.addEventListener("change", () => {
      const engine = engineSelect.value;
      if (!isDualLanguageEnabled(engine) || !secondaryLanguageSelect) {
        return;
      }
      const language = getEdgeVoiceLanguage(secondaryVoiceSelect.value, engine);
      const catalog = getEngineVoiceCatalog(engine);
      if (language && catalog[language] && secondaryLanguageSelect.value !== language) {
        secondaryLanguageSelect.value = language;
      }
    });

    dualPauseModeSelect?.addEventListener("change", () => {
      setDualPauseSettings({
        mode: dualPauseModeSelect.value,
        pauseMs: dualPauseMsInput?.value,
      });
      updateEdgeControlsForEngine(engineSelect.value);
    });

    dualPauseMsInput?.addEventListener("change", () => {
      dualPauseMsInput.value = String(clampDualPauseMs(dualPauseMsInput.value, dualPauseConfig.ms?.default ?? 90));
      updateEdgeControlsForEngine(engineSelect.value);
    });

    edgeParallelismSelect?.addEventListener("change", () => {
      edgeParallelismSelect.value = String(getEdgeParallelismValue(engineSelect.value));
    });

    googleRetrySelect?.addEventListener("change", () => {
      googleRetrySelect.value = String(getGoogleRetryCountValue());
    });

    edgeRateInput?.addEventListener("change", () => {
      setEdgeInputValue(edgeRateInput, "rate", edgeRateInput.value);
    });
    edgePitchInput?.addEventListener("change", () => {
      setEdgeInputValue(edgePitchInput, "pitch", edgePitchInput.value);
    });
    edgeVolumeInput?.addEventListener("change", () => {
      setEdgeInputValue(edgeVolumeInput, "volume", edgeVolumeInput.value);
    });
    secondaryRateInput?.addEventListener("change", () => {
      setEdgeInputValue(secondaryRateInput, "rate", secondaryRateInput.value);
    });
    secondaryPitchInput?.addEventListener("change", () => {
      setEdgeInputValue(secondaryPitchInput, "pitch", secondaryPitchInput.value);
    });
    secondaryVolumeInput?.addEventListener("change", () => {
      setEdgeInputValue(secondaryVolumeInput, "volume", secondaryVolumeInput.value);
    });

    edgeNormalizerPreset?.addEventListener("change", () => {
      const selected = String(edgeNormalizerPreset.value || "");
      if (selected && selected !== "__custom__") {
        edgeNormalizerLastPreset = selected;
        applyEdgeNormalizerPreset(selected, true);
      } else {
        updateEdgeNormalizerSummary();
      }
      updateEdgeControlsForEngine(engineSelect.value);
      queueEdgeNormalizerAnalysis({ delayMs: 120 });
    });

    edgeNormalizerEnabled?.addEventListener("change", () => {
      markEdgeNormalizerPresetCustom();
      updateEdgeNormalizerSummary();
      updateEdgeControlsForEngine(engineSelect.value);
      queueEdgeNormalizerAnalysis({ delayMs: 120 });
    });

    const edgeNormalizerCustomControls = [
      edgeNormalizerAuto,
      edgeNormalizerStripMarkdown,
      edgeNormalizerUnwrapMarkdownLinks,
      edgeNormalizerStripUrls,
      edgeNormalizerStripEmails,
      edgeNormalizerCollapseSymbols,
      edgeNormalizerCollapsePunctuation,
      edgeNormalizerPreserveEllipsis,
      edgeNormalizerDropSymbolTokens,
      edgeNormalizerWhitespace,
    ];
    edgeNormalizerCustomControls.forEach((control) => {
      control?.addEventListener("change", () => {
        markEdgeNormalizerPresetCustom();
        updateEdgeNormalizerSummary();
        updateEdgeControlsForEngine(engineSelect.value);
        queueEdgeNormalizerAnalysis({ delayMs: 140 });
      });
    });

    edgeNormalizerCustomSymbolsInput?.addEventListener("input", () => {
      setEdgeNormalizerSelectedDropSymbols(edgeNormalizerCustomSymbolsInput.value, false);
      const normalizedText = getSelectedDropSymbolsFromUI().join("");
      if (edgeNormalizerCustomSymbolsInput.value !== normalizedText) {
        edgeNormalizerCustomSymbolsInput.value = normalizedText;
      }
      renderEdgeNormalizerSymbols(edgeNormalizerLastSymbolDelta);
      markEdgeNormalizerPresetCustom();
      updateEdgeNormalizerSummary();
      updateEdgeControlsForEngine(engineSelect.value);
      queueEdgeNormalizerAnalysis({ delayMs: 140 });
    });

    edgeNormalizerAnalyzerAuto?.addEventListener("change", () => {
      if (toBool(edgeNormalizerAnalyzerAuto.checked, true)) {
        queueEdgeNormalizerAnalysis({ delayMs: 40, force: true });
      } else {
        setEdgeNormalizerAnalyzerStatus("Автоанализ отключен. При необходимости нажмите «Анализировать».", false);
      }
    });

    edgeNormalizerAnalyzeButton?.addEventListener("click", () => {
      queueEdgeNormalizerAnalysis({ delayMs: 0, force: true });
    });

    textArea.addEventListener("input", () => {
      queueEdgeNormalizerAnalysis({ delayMs: 320 });
    });

    textArea.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
        event.preventDefault();
        runSynthesis();
      }
    });

    historyClearButton?.addEventListener("click", () => {
      if (!historyAvailable) {
        setOutput(messages.historyUnavailable, false);
        return;
      }
      saveHistory([]);
      renderHistory();
      setOutput(messages.historyCleared, true);
    });

    resetSourceMeta();
    updateFileInfo();
    applyEdgeOptionsConfig("Edge TTS", edgeOptionsConfig, true);
    applyEdgeNormalizerConfig({}, true);
    clearEdgeNormalizerAnalyzer("Вставьте текст или импортируйте файл для запуска анализатора.");
    renderHistory();
    renderDiagnostics([], false);
    setInstallLogStatus(messages.installLogIdle, "info");
    renderInstallLog([], { append: false, open: false });
    renderLiveLog({ status: "idle", percent: 0, message: messages.liveLogIdle, logs: [] });
    await loadConfig();
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => {
      init();
    });
  } else {
    init();
  }
})();
