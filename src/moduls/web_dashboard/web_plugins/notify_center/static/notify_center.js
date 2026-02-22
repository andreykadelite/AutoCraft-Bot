(() => {
  const init = () => {
    const messages = {
      emptyText: "Текст уведомления пустой.",
      sendUrlMissing:
        "Не удалось определить адрес отправки. Обновите страницу.",
      csrfMissing:
        "Не удалось получить токен защиты. Обновите страницу.",
      sending: "Отправка уведомления...",
      sentOk: "Уведомление отправлено.",
      sessionExpired:
        "Нет доступа или истекла сессия. Обновите страницу.",
      serverStatus: (status) =>
        `Ошибка отправки. Статус сервера: ${status}.`,
      sendError: "Не удалось отправить уведомление.",
      historyUnavailable:
        "История недоступна в этом браузере.",
      historyEmpty: "Журнал пока пуст.",
      historyCleared: "Журнал очищен.",
      noTitle: "Без заголовка",
    };

    const root = document.getElementById("notify-center-root");
    if (!root) {
      return;
    }

    const config = {
      sendUrl: root.dataset.sendUrl || "",
      csrfToken: root.dataset.csrfToken || "",
      maxTitle: Number(root.dataset.maxTitle || 80),
      maxText: Number(root.dataset.maxText || 500),
      historyLimit: Number(root.dataset.historyLimit || 12),
    };

    const titleInput = document.getElementById("notify-title");
    const textInput = document.getElementById("notify-text");
    const sendButton = document.getElementById("notify-send");
    const clearButton = document.getElementById("notify-clear");
    const output = document.getElementById("notify-output");
    const counter = document.getElementById("notify-counter");
    const historyBox = document.getElementById("notify-history");
    const historyClear = document.getElementById("notify-history-clear");
    const templateButtons = document.querySelectorAll(".notify-template");

    if (!textInput || !sendButton || !output) {
      return;
    }

    const historyKey = "notify_center.history";
    let historyAvailable = true;
    let sending = false;

    const setOutput = (message, ok) => {
      const isError = ok === false;
      output.textContent = message;
      output.classList.toggle("is-ok", Boolean(ok));
      output.classList.toggle("is-error", isError);
      output.setAttribute("aria-live", isError ? "assertive" : "polite");
    };

    const updateCounter = () => {
      if (!counter) {
        return;
      }
      const current = (textInput.value || "").trim().length;
      counter.textContent = `${current} / ${config.maxText}`;
    };

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

    const formatTime = (value) => {
      try {
        return new Date(value).toLocaleTimeString("ru-RU", {
          hour: "2-digit",
          minute: "2-digit",
        });
      } catch (_err) {
        return "";
      }
    };

    const updateHistoryControls = (hasItems) => {
      if (!historyClear) {
        return;
      }
      historyClear.disabled = !historyAvailable || !hasItems;
    };

    const renderHistory = () => {
      if (!historyBox) {
        return;
      }
      const items = loadHistory();
      historyBox.innerHTML = "";
      if (!historyAvailable) {
        const empty = document.createElement("div");
        empty.className = "notify-history-empty";
        empty.textContent = messages.historyUnavailable;
        historyBox.appendChild(empty);
        updateHistoryControls(false);
        return;
      }
      if (!items.length) {
        const empty = document.createElement("div");
        empty.className = "notify-history-empty";
        empty.textContent = messages.historyEmpty;
        historyBox.appendChild(empty);
        updateHistoryControls(false);
        return;
      }

      items.forEach((item) => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "notify-history-item";
        button.setAttribute("role", "listitem");

        const title = document.createElement("div");
        title.className = "notify-history-title";
        title.textContent = item.title || messages.noTitle;

        const text = document.createElement("div");
        text.className = "notify-history-text";
        text.textContent = item.text || "";

        const time = document.createElement("div");
        time.className = "notify-history-time";
        time.textContent = formatTime(item.ts);

        button.append(title, text, time);
        button.addEventListener("click", () => {
          if (titleInput) {
            titleInput.value = item.title || "";
          }
          textInput.value = item.text || "";
          updateCounter();
          textInput.focus();
        });
        historyBox.appendChild(button);
      });
      updateHistoryControls(true);
    };

    const addToHistory = (title, text) => {
      const items = loadHistory();
      const entry = {
        title: title || "",
        text,
        ts: Date.now(),
      };
      items.unshift(entry);
      const trimmed = items.slice(0, config.historyLimit);
      saveHistory(trimmed);
      renderHistory();
    };

    const sendNotification = async () => {
      if (sending) {
        return;
      }
      const title = (titleInput?.value || "").trim();
      const text = textInput.value.trim();
      if (!text) {
        setOutput(messages.emptyText, false);
        return;
      }
      if (!config.sendUrl) {
        setOutput(messages.sendUrlMissing, false);
        return;
      }
      if (!config.csrfToken) {
        setOutput(messages.csrfMissing, false);
        return;
      }

      sending = true;
      sendButton.disabled = true;
      sendButton.setAttribute("aria-disabled", "true");
      output.setAttribute("aria-busy", "true");
      setOutput(messages.sending, true);
      try {
        const response = await fetch(config.sendUrl, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-CSRFToken": config.csrfToken || "",
            Accept: "application/json",
          },
          credentials: "same-origin",
          body: JSON.stringify({
            title,
            message: text,
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
          const note = data.note ? ` ${data.note}` : "";
          setOutput((data.message || messages.sentOk) + note, true);
          addToHistory(title, text);
        } else if (!data) {
          if (response.status === 401 || response.status === 403) {
            setOutput(messages.sessionExpired, false);
          } else {
            setOutput(messages.serverStatus(response.status), false);
          }
        } else {
          setOutput(data.error || data.message || messages.sendError, false);
        }
      } catch (_err) {
        setOutput(messages.sendError, false);
      } finally {
        output.setAttribute("aria-busy", "false");
        sendButton.disabled = false;
        sendButton.removeAttribute("aria-disabled");
        sending = false;
      }
    };

    sendButton.addEventListener("click", () => sendNotification());

    textInput.addEventListener("input", updateCounter);
    textInput.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
        event.preventDefault();
        sendNotification();
      }
      if (event.key === "Escape") {
        textInput.value = "";
        updateCounter();
        textInput.focus();
      }
    });

    clearButton?.addEventListener("click", () => {
      if (titleInput) {
        titleInput.value = "";
      }
      textInput.value = "";
      updateCounter();
      textInput.focus();
    });

    templateButtons.forEach((button) => {
      button.addEventListener("click", () => {
        const templateTitle = button.dataset.title || "";
        const templateText = button.dataset.text || "";
        if (titleInput) {
          titleInput.value = templateTitle;
        }
        textInput.value = templateText;
        updateCounter();
        textInput.focus();
      });
    });

    historyClear?.addEventListener("click", () => {
      if (!historyAvailable) {
        setOutput(messages.historyUnavailable, false);
        return;
      }
      saveHistory([]);
      renderHistory();
      setOutput(messages.historyCleared, true);
    });

    updateCounter();
    renderHistory();
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
