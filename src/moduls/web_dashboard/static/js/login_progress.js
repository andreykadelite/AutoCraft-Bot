(function () {
  var root = document.getElementById("login-progress");
  if (!root) {
    return;
  }

  var statusUrl = root.getAttribute("data-status-url");
  var nextUrl = root.getAttribute("data-next-url");
  var bar = document.getElementById("login-progress-bar");
  var percentText = document.getElementById("login-progress-percent");
  var statusEl = document.getElementById("login-progress-status");
  var stepsEl = document.getElementById("login-progress-steps");
  var continueBtn = document.getElementById("login-progress-continue");

  var statusLabels = {
    pending: "Ожидание",
    in_progress: "Выполняется",
    done: "Готово",
    error: "Ошибка",
  };
  var pollDelayMs = 900;
  var errorDelayMs = 2000;
  var lastPercent = null;
  var lastMessage = "";

  function normalizeText(value) {
    if (value === null || value === undefined) {
      return "";
    }
    return String(value).trim();
  }

  function clampPercent(value) {
    var num = Number(value);
    if (!Number.isFinite(num)) {
      return 0;
    }
    return Math.max(0, Math.min(100, Math.round(num)));
  }

  function stripStatusPrefix(value) {
    var text = normalizeText(value);
    if (!text) {
      return "";
    }
    return text.replace(/^Статус\s*:?\s*/i, "").trim();
  }

  function ensureStepMarkup(item) {
    if (!item) {
      return {
        labelNode: null,
        statusWrap: null,
        statusNode: null,
      };
    }

    var labelNode = item.querySelector(".login-progress-step-label");
    var statusWrap = item.querySelector(".login-progress-step-status");
    if (!statusWrap) {
      statusWrap = document.createElement("span");
      statusWrap.className = "login-progress-step-status";
      item.appendChild(statusWrap);
    }

    if (
      labelNode &&
      statusWrap &&
      !item.querySelector(".login-progress-step-separator")
    ) {
      var sep = document.createElement("span");
      sep.className = "login-progress-step-separator";
      sep.setAttribute("aria-hidden", "true");
      sep.textContent = "\u00A0—\u00A0";
      labelNode.insertAdjacentElement("afterend", sep);
    }

    var statusNode = statusWrap.querySelector("[data-step-status-text]");
    var initialText = "";
    if (statusNode) {
      initialText = normalizeText(statusNode.textContent);
    } else {
      initialText = stripStatusPrefix(statusWrap.textContent);
      statusNode = document.createElement("span");
      statusNode.setAttribute("data-step-status-text", "");
    }

    var prefixNode = document.createElement("span");
    prefixNode.className = "login-progress-step-status-prefix";
    prefixNode.textContent = "Статус:\u00A0";

    statusWrap.textContent = "";
    statusWrap.appendChild(prefixNode);
    statusWrap.appendChild(statusNode);

    if (initialText) {
      statusNode.textContent = initialText;
    }

    return {
      labelNode: labelNode,
      statusWrap: statusWrap,
      statusNode: statusNode,
    };
  }

  function normalizeStepsMarkup() {
    if (!stepsEl) {
      return;
    }
    var items = stepsEl.querySelectorAll(".login-progress-step");
    Array.prototype.forEach.call(items, function (item) {
      var refs = ensureStepMarkup(item);
      var stepLabel = normalizeText(item.getAttribute("data-step-label"));
      if (!stepLabel) {
        stepLabel = normalizeText(refs.labelNode ? refs.labelNode.textContent : "");
      }
      var statusText = normalizeText(
        refs.statusNode ? refs.statusNode.textContent : ""
      );
      var stateKey = normalizeText(item.getAttribute("data-step-status")) || "pending";
      var stateLabel = statusLabels[stateKey] || statusLabels.pending;
      var ariaStatusText = statusText;
      if (statusText && statusText !== stateLabel) {
        ariaStatusText = stateLabel + ". " + statusText;
      }
      if (stepLabel && ariaStatusText) {
        item.setAttribute("aria-label", stepLabel + ": " + ariaStatusText);
      }
    });
  }

  function updateSteps(steps) {
    if (!stepsEl || !Array.isArray(steps)) {
      return;
    }
    steps.forEach(function (step) {
      var id = step && step.id ? String(step.id) : "";
      if (!id) {
        return;
      }
      var item = stepsEl.querySelector('[data-step-id="' + id + '"]');
      if (!item) {
        return;
      }
      var status = step.status || "pending";
      item.setAttribute("data-step-status", status);
      if (status === "in_progress") {
        item.setAttribute("aria-current", "step");
      } else {
        item.removeAttribute("aria-current");
      }

      var refs = ensureStepMarkup(item);
      var labelNode = refs.labelNode;
      var statusNode = refs.statusNode;

      var stepLabel = item.getAttribute("data-step-label") || "";
      if (!stepLabel) {
        stepLabel = labelNode ? labelNode.textContent || "" : "";
      }
      stepLabel = String(stepLabel).trim();

      var stateLabel = statusLabels[status] || statusLabels.pending;
      var detailText = normalizeText(step.detail);
      var statusText = detailText || stateLabel;
      var ariaStatusText = detailText ? stateLabel + ". " + detailText : stateLabel;

      if (statusNode) {
        statusNode.textContent = statusText;
      }

      if (stepLabel && ariaStatusText) {
        item.setAttribute("aria-label", stepLabel + ": " + ariaStatusText);
      }
    });
  }

  function updateProgress(data) {
    if (!data) {
      return;
    }
    var percent = clampPercent(data.percent);
    if (bar && lastPercent !== percent) {
      bar.style.width = percent + "%";
      bar.setAttribute("aria-valuenow", String(percent));
      bar.setAttribute("aria-valuetext", "Готово на " + percent + "%");
    }
    if (percentText && lastPercent !== percent) {
      percentText.textContent = percent + "%";
    }
    var message = data.message || "Вход выполняется";
    if (statusEl && message !== lastMessage) {
      statusEl.textContent = message;
    }
    lastPercent = percent;
    lastMessage = message;
    root.setAttribute("aria-busy", data.done ? "false" : "true");
    updateSteps(data.steps || []);
  }

  function showContinue(data) {
    if (!continueBtn) {
      return;
    }
    continueBtn.hidden = false;
    if (data && data.next_url) {
      continueBtn.setAttribute("href", data.next_url);
    } else if (nextUrl) {
      continueBtn.setAttribute("href", nextUrl);
    }
  }

  function scheduleNext(delayMs) {
    window.setTimeout(poll, delayMs);
  }

  function poll() {
    if (!statusUrl) {
      return;
    }
    fetch(statusUrl, {
      credentials: "same-origin",
      cache: "no-store",
      headers: { Accept: "application/json" },
    })
      .then(function (response) {
        if (!response.ok) {
          throw new Error("bad response");
        }
        return response.json();
      })
      .then(function (data) {
        updateProgress(data);
        if (data.done) {
          showContinue(data);
          if (!data.error && (data.next_url || nextUrl)) {
            if (statusEl) {
              statusEl.textContent = "Подготовка завершена. Переходим в панель.";
            }
            window.setTimeout(function () {
              window.location.assign(data.next_url || nextUrl);
            }, 800);
          } else if (data.error && statusEl) {
            statusEl.textContent = data.error;
          }
          return;
        }
        scheduleNext(pollDelayMs);
      })
      .catch(function () {
        if (statusEl) {
          statusEl.textContent =
            "Не удается получить статус подготовки. Повторим через несколько секунд.";
        }
        scheduleNext(errorDelayMs);
      });
  }

  normalizeStepsMarkup();
  poll();
})();
