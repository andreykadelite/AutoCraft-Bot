(() => {
  const init = () => {
    const messages = {
      historyUnavailable:
        "\u0418\u0441\u0442\u043e\u0440\u0438\u044f \u043d\u0435\u0434\u043e\u0441\u0442\u0443\u043f\u043d\u0430 \u0432 \u044d\u0442\u043e\u043c \u0431\u0440\u0430\u0443\u0437\u0435\u0440\u0435.",
      historyEmpty:
        "\u0418\u0441\u0442\u043e\u0440\u0438\u044f \u043f\u043e\u043a\u0430 \u043f\u0443\u0441\u0442\u0430.",
      commandEmpty: "\u041a\u043e\u043c\u0430\u043d\u0434\u0430 \u043f\u0443\u0441\u0442\u0430\u044f.",
      runUrlMissing:
        "\u041d\u0435 \u0443\u0434\u0430\u043b\u043e\u0441\u044c \u043e\u043f\u0440\u0435\u0434\u0435\u043b\u0438\u0442\u044c \u0430\u0434\u0440\u0435\u0441 \u0432\u044b\u043f\u043e\u043b\u043d\u0435\u043d\u0438\u044f. \u041e\u0431\u043d\u043e\u0432\u0438\u0442\u0435 \u0441\u0442\u0440\u0430\u043d\u0438\u0446\u0443.",
      csrfMissing:
        "\u041d\u0435 \u0443\u0434\u0430\u043b\u043e\u0441\u044c \u043f\u043e\u043b\u0443\u0447\u0438\u0442\u044c \u0442\u043e\u043a\u0435\u043d \u0437\u0430\u0449\u0438\u0442\u044b. \u041e\u0431\u043d\u043e\u0432\u0438\u0442\u0435 \u0441\u0442\u0440\u0430\u043d\u0438\u0446\u0443.",
      running: "\u0412\u044b\u043f\u043e\u043b\u043d\u0435\u043d\u0438\u0435...",
      commandDone:
        "\u041a\u043e\u043c\u0430\u043d\u0434\u0430 \u0432\u044b\u043f\u043e\u043b\u043d\u0435\u043d\u0430.",
      sessionExpired:
        "\u041d\u0435\u0442 \u0434\u043e\u0441\u0442\u0443\u043f\u0430 \u0438\u043b\u0438 \u0438\u0441\u0442\u0435\u043a\u043b\u0430 \u0441\u0435\u0441\u0441\u0438\u044f. \u041e\u0431\u043d\u043e\u0432\u0438\u0442\u0435 \u0441\u0442\u0440\u0430\u043d\u0438\u0446\u0443.",
      serverStatus: (status) =>
        `\u041e\u0448\u0438\u0431\u043a\u0430 \u0432\u044b\u043f\u043e\u043b\u043d\u0435\u043d\u0438\u044f. \u0421\u0442\u0430\u0442\u0443\u0441 \u0441\u0435\u0440\u0432\u0435\u0440\u0430: ${status}.`,
      execError:
        "\u041e\u0448\u0438\u0431\u043a\u0430 \u0432\u044b\u043f\u043e\u043b\u043d\u0435\u043d\u0438\u044f.",
      sendError:
        "\u041d\u0435 \u0443\u0434\u0430\u043b\u043e\u0441\u044c \u043e\u0442\u043f\u0440\u0430\u0432\u0438\u0442\u044c \u043a\u043e\u043c\u0430\u043d\u0434\u0443.",
      copyOk:
        "\u041a\u043e\u043c\u0430\u043d\u0434\u0430 \u0441\u043a\u043e\u043f\u0438\u0440\u043e\u0432\u0430\u043d\u0430.",
      copyFail:
        "\u041d\u0435 \u0443\u0434\u0430\u043b\u043e\u0441\u044c \u0441\u043a\u043e\u043f\u0438\u0440\u043e\u0432\u0430\u0442\u044c \u043a\u043e\u043c\u0430\u043d\u0434\u0443.",
      historyCleared:
        "\u0418\u0441\u0442\u043e\u0440\u0438\u044f \u043e\u0447\u0438\u0449\u0435\u043d\u0430.",
    };
    const config = window.winRunConfig || {};
    const root = document.getElementById("winrun-root");
    if (root) {
      if (!config.runUrl) {
        config.runUrl = root.dataset.runUrl || "";
      }
      if (!config.csrfToken) {
        config.csrfToken = root.dataset.csrfToken || "";
      }
    }
    const input = document.getElementById("winrun-command");
    const runButton = document.getElementById("winrun-run");
    const clearButton = document.getElementById("winrun-clear");
    const copyButton = document.getElementById("winrun-copy");
    const output = document.getElementById("winrun-output");
    const historyBox = document.getElementById("winrun-history");
    const adminToggle = document.getElementById("winrun-admin");
    const historyClearButton = document.getElementById("winrun-history-clear");
    const historyKey = "winrun.history";
    const historyLimit = Number(config.historyLimit || 15);

    if (!input || !runButton || !output || !historyBox) {
      return;
    }

    let historyAvailable = true;
    let running = false;

    const loadHistory = () => {
      try {
        const raw = localStorage.getItem(historyKey);
        const data = raw ? JSON.parse(raw) : [];
        return Array.isArray(data) ? data : [];
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

    const updateHistoryControls = (hasItems) => {
      if (!historyClearButton) {
        return;
      }
      historyClearButton.disabled = !historyAvailable || !hasItems;
    };

    const renderHistory = () => {
      const items = loadHistory();
      historyBox.innerHTML = "";
      if (!historyAvailable) {
        const empty = document.createElement("div");
        empty.className = "winrun-history-empty";
        empty.textContent = messages.historyUnavailable;
        historyBox.appendChild(empty);
        updateHistoryControls(false);
        return;
      }
      if (!items.length) {
        const empty = document.createElement("div");
        empty.className = "winrun-history-empty";
        empty.textContent = messages.historyEmpty;
        historyBox.appendChild(empty);
        updateHistoryControls(false);
        return;
      }

      items.forEach((cmd) => {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "winrun-history-item";
        btn.setAttribute("role", "listitem");
        btn.textContent = cmd;
        btn.addEventListener("click", () => {
          input.value = cmd;
          input.focus();
        });
        historyBox.appendChild(btn);
      });
      updateHistoryControls(true);
    };

    const addToHistory = (cmd) => {
      const items = loadHistory();
      const filtered = items.filter((item) => item !== cmd);
      filtered.unshift(cmd);
      const trimmed = filtered.slice(0, historyLimit);
      saveHistory(trimmed);
      renderHistory();
    };

    const setOutput = (message, ok) => {
      const isError = ok === false;
      output.textContent = message;
      output.classList.toggle("is-ok", Boolean(ok));
      output.classList.toggle("is-error", isError);
      output.setAttribute("aria-live", isError ? "assertive" : "polite");
    };

    const runCommand = async (options = {}) => {
      if (running) {
        return;
      }
      const cmd = input.value.trim();
      if (!cmd) {
        setOutput(messages.commandEmpty, false);
        return;
      }

      if (!config.runUrl) {
        setOutput(messages.runUrlMissing, false);
        return;
      }
      if (!config.csrfToken) {
        setOutput(messages.csrfMissing, false);
        return;
      }

      const runAsAdmin = Boolean(
        options.runAsAdmin || (adminToggle && adminToggle.checked)
      );

      running = true;
      runButton.disabled = true;
      runButton.setAttribute("aria-disabled", "true");
      output.setAttribute("aria-busy", "true");
      setOutput(messages.running, true);
      try {
        const response = await fetch(config.runUrl, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-CSRFToken": config.csrfToken || "",
            "Accept": "application/json",
          },
          credentials: "same-origin",
          body: JSON.stringify({
            command: cmd,
            run_as_admin: runAsAdmin,
            csrf_token: config.csrfToken || "",
          }),
        });
        let data = null;
        try {
          data = await response.json();
        } catch (_err) {
          data = null;
        }
        if (data && data.ok) {
          setOutput(data.message || messages.commandDone, true);
          addToHistory(cmd);
        } else if (!data) {
          if (response.status === 401 || response.status === 403) {
            setOutput(messages.sessionExpired, false);
          } else {
            setOutput(messages.serverStatus(response.status), false);
          }
        } else {
          const msg = data.error || data.message;
          setOutput(msg || messages.execError, false);
        }
      } catch (_err) {
        setOutput(messages.sendError, false);
      } finally {
        output.setAttribute("aria-busy", "false");
        runButton.disabled = false;
        runButton.removeAttribute("aria-disabled");
        running = false;
      }
    };

    runButton.addEventListener("click", () => runCommand());
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        runCommand({ runAsAdmin: event.shiftKey });
      }
      if (event.key === "Escape") {
        input.value = "";
        input.focus();
      }
    });

    clearButton?.addEventListener("click", () => {
      input.value = "";
      input.focus();
    });

    copyButton?.addEventListener("click", async () => {
      const cmd = input.value.trim();
      if (!cmd || !navigator.clipboard) {
        return;
      }
      try {
        await navigator.clipboard.writeText(cmd);
        setOutput(messages.copyOk, true);
      } catch (_err) {
        setOutput(messages.copyFail, false);
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
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
