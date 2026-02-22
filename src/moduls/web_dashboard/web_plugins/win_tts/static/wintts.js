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
        "Устанавливаю зависимости TTS. Это может занять несколько минут...",
      installDone: "Установка зависимостей завершена.",
      installFail: "Не удалось установить зависимости автоматически.",
      diagnosticsEmpty: "Диагностика зависимостей недоступна.",
      historyEmpty: "История пока пустая.",
      historyUnavailable:
        "История недоступна в этом браузере (нет доступа к localStorage).",
      historyCleared: "История очищена.",
    };

    const root = document.getElementById("wintts-root");
    const engineSelect = document.getElementById("wintts-engine");
    const voiceSelect = document.getElementById("wintts-voice");
    const textArea = document.getElementById("wintts-text");
    const runButton = document.getElementById("wintts-run");
    const clearButton = document.getElementById("wintts-clear");
    const installButton = document.getElementById("wintts-install");
    const output = document.getElementById("wintts-output");
    const diagnosticsBox = document.getElementById("wintts-diagnostics");
    const audioSection = document.getElementById("wintts-audio-section");
    const audioPlayer = document.getElementById("wintts-audio");
    const downloadLink = document.getElementById("wintts-download");
    const historyBox = document.getElementById("wintts-history");
    const historyClearButton = document.getElementById("wintts-history-clear");

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
      installUrl: root.dataset.installUrl || "",
      csrfToken: root.dataset.csrfToken || "",
    };

    const historyKey = "wintts.history";
    const historyLimit = 20;
    let historyAvailable = true;
    let busy = false;
    let voicesByEngine = {};
    let maxTextLen = 5000;

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
        return { data: null, rawText: "", parseError: "empty response" };
      }
      try {
        return { data: JSON.parse(rawText), rawText, parseError: "" };
      } catch (err) {
        return {
          data: null,
          rawText,
          parseError: err && err.message ? String(err.message) : "invalid JSON",
        };
      }
    };

    const toBodySnippet = (text, maxLen = 600) => {
      const normalized = String(text || "").replace(/\\r/g, "").trim();
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
        "Похоже, сервер вернул не JSON (возможно, ошибка backend/доступа или редирект на страницу входа)."
      );
      renderDiagnostics(lines, true);
    };

    const setBusy = (state) => {
      busy = state;
      runButton.disabled = state;
      runButton.setAttribute("aria-disabled", state ? "true" : "false");
      clearButton.disabled = state;
      if (installButton) {
        installButton.disabled = state;
      }
      output.setAttribute("aria-busy", state ? "true" : "false");
      diagnosticsBox.setAttribute("aria-busy", state ? "true" : "false");
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
        const title = `${item.engine || ""} | ${item.voice || ""}`;
        const shortText = (item.text || "").slice(0, 110);
        btn.textContent = `${title} :: ${shortText}${shortText.length >= 110 ? "..." : ""}`;
        btn.title = ts ? `${ts}\n${item.text || ""}` : item.text || "";
        btn.addEventListener("click", () => {
          if (item.engine) {
            engineSelect.value = item.engine;
            renderVoices(item.engine, item.voice || "");
          }
          textArea.value = item.text || "";
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

    const clearSelect = (selectNode) => {
      while (selectNode.options.length) {
        selectNode.remove(0);
      }
    };

    const renderVoices = (engine, selectedVoice = "") => {
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

    const renderEngines = (engines, voices, selectedEngine = "") => {
      voicesByEngine = voices || {};
      clearSelect(engineSelect);
      (engines || []).forEach((engine) => {
        const option = document.createElement("option");
        option.value = engine;
        option.textContent = engine;
        engineSelect.appendChild(option);
      });
      if (engineSelect.options.length) {
        const target =
          selectedEngine && engines.includes(selectedEngine)
            ? selectedEngine
            : engineSelect.options[0].value;
        engineSelect.value = target;
        renderVoices(target);
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

        renderEngines(data.engines || [], data.voices || {});
        maxTextLen = Number(data.max_text_len || 5000);
        textArea.maxLength = maxTextLen;

        if (installButton) {
          installButton.hidden = !Boolean(data.can_install);
        }

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

    const runSynthesis = async () => {
      if (busy) {
        return;
      }

      const text = textArea.value.trim();
      if (!text) {
        setOutput(messages.textEmpty, false);
        return;
      }
      if (text.length > maxTextLen) {
        setOutput(`Текст слишком длинный. Максимум ${maxTextLen} символов.`, false);
        return;
      }
      if (!config.synthUrl) {
        setOutput(messages.synthFail, false);
        return;
      }
      if (!config.csrfToken) {
        setOutput(messages.csrfMissing, false);
        return;
      }

      setBusy(true);
      setOutput(messages.synthRunning, true);
      const engine = engineSelect.value;
      const voice = voiceSelect.value;

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
            text,
            engine,
            voice,
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
        if (data.audio_url && data.download_url) {
          setAudioResult(data.audio_url, data.download_url, data.filename || "");
          addHistory({
            ts: Math.floor(Date.now() / 1000),
            engine,
            voice,
            text,
            audioUrl: data.audio_url,
            downloadUrl: data.download_url,
            filename: data.filename || "",
          });
        }
        renderDiagnostics(data.diagnostics_lines || [], false);
      } catch (err) {
        setOutput(messages.sendFail, false);
        const message =
          err && err.message
            ? `Ошибка сети при синтезе: ${String(err.message)}`
            : "Ошибка сети при синтезе.";
        renderDiagnostics([message], true);
      } finally {
        setBusy(false);
      }
    };

    const installDependencies = async () => {
      if (busy || !config.installUrl) {
        return;
      }
      if (!config.csrfToken) {
        setOutput(messages.csrfMissing, false);
        return;
      }

      setBusy(true);
      setOutput(messages.installRunning, true);

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
          renderHttpFallbackDiagnostics(response, parseError, rawText);
          return;
        }

        if (!response.ok || !data.ok) {
          setOutput(data.error || data.message || messages.installFail, false);
        } else {
          setOutput(data.message || messages.installDone, true);
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
            ? `Ошибка сети при установке зависимостей: ${String(err.message)}`
            : "Ошибка сети при установке зависимостей.";
        renderDiagnostics([message], true);
      } finally {
        setBusy(false);
      }
    };

    runButton.addEventListener("click", runSynthesis);

    clearButton.addEventListener("click", () => {
      textArea.value = "";
      textArea.focus();
    });

    installButton?.addEventListener("click", installDependencies);

    engineSelect.addEventListener("change", () => {
      renderVoices(engineSelect.value);
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

    renderHistory();
    renderDiagnostics([], false);
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
