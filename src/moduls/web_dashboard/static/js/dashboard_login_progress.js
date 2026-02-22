(function () {
  "use strict";

  function onReady(fn) {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", fn, { once: true });
    } else {
      fn();
    }
  }

  var statusLabels = {
    pending: "Ожидание",
    in_progress: "Выполняется",
    done: "Готово",
    error: "Ошибка",
  };

  function normalizeText(value) {
    if (value === null || value === undefined) {
      return "";
    }
    return String(value).trim();
  }

  function getValueByPath(data, path) {
    if (!data || !path) {
      return undefined;
    }
    var parts = String(path).split(".");
    var current = data;
    for (var i = 0; i < parts.length; i += 1) {
      if (!current || typeof current !== "object") {
        return undefined;
      }
      current = current[parts[i]];
    }
    return current;
  }

  function formatValue(path, value) {
    if (value === null || value === undefined) {
      return "-";
    }
    if (typeof value === "number" && Number.isFinite(value)) {
      if (path.indexOf("percent") !== -1) {
        return value.toFixed(1).replace(/\\.0$/, "");
      }
      if (path.indexOf("_gb") !== -1) {
        return value.toFixed(2).replace(/\\.00$/, "");
      }
      if (path.indexOf("packets") !== -1) {
        return Math.round(value).toString();
      }
    }
    return String(value);
  }

  onReady(function () {
    var root = document.getElementById("dashboard-login-progress");
    if (!root) {
      return;
    }

    var statusUrl = root.getAttribute("data-login-progress-status-url");
    if (!statusUrl) {
      return;
    }

    var messageEl = root.querySelector("[data-login-progress-message]");
    var stepsEl = root.querySelector("[data-login-progress-steps]");
    var retryWrap = root.querySelector("[data-login-progress-retry-wrap]");
    var liveEl = document.getElementById("overview-live");
    var overviewContainer = document.querySelector("[data-overview-url]");
    var overviewUpdated = document.querySelector("[data-overview-updated]");

    var done = root.getAttribute("data-login-progress-done") === "1";
    var failed = root.getAttribute("data-login-progress-has-error") === "1";
    var reloadScheduled = false;
    var reloadStorageKey = "dashboard_login_progress_reload:" + statusUrl;

    function announce(text) {
      if (!liveEl || !text) {
        return;
      }
      liveEl.textContent = text;
    }

    function setMessage(text) {
      if (!messageEl) {
        return;
      }
      var value = normalizeText(text);
      if (!value) {
        return;
      }
      messageEl.textContent = value;
    }

    function setRetryVisible(visible) {
      if (retryWrap) {
        retryWrap.hidden = !visible;
      }
    }

    function setStateClass(cls) {
      root.classList.remove("alert-info");
      root.classList.remove("alert-warning");
      root.classList.remove("alert-success");
      root.classList.add(cls);
    }

    function reloadOverviewOnce() {
      if (reloadScheduled) {
        return;
      }
      try {
        if (window.sessionStorage.getItem(reloadStorageKey) === "1") {
          return;
        }
        window.sessionStorage.setItem(reloadStorageKey, "1");
      } catch (err) {
        // Ignore storage issues and continue with one reload in current runtime.
      }
      reloadScheduled = true;
      window.setTimeout(function () {
        window.location.reload();
      }, 350);
    }

    function stepStateLabel(step) {
      if (!step || typeof step !== "object") {
        return statusLabels.pending;
      }
      var state = normalizeText(step.status) || "pending";
      return statusLabels[state] || statusLabels.pending;
    }

    function stepStatusText(step) {
      if (!step || typeof step !== "object") {
        return statusLabels.pending;
      }
      var detail = normalizeText(step.detail);
      if (detail) {
        return detail;
      }
      return stepStateLabel(step);
    }

    function stepAriaStatusText(step) {
      var label = stepStateLabel(step);
      var detail = normalizeText(step && step.detail);
      if (detail) {
        return label + ". " + detail;
      }
      return label;
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
          textNode: null,
        };
      }

      var labelNode = item.querySelector(".dashboard-login-progress-step-label");
      var statusWrap = item.querySelector(".dashboard-login-progress-step-status");
      if (!statusWrap) {
        statusWrap = document.createElement("span");
        statusWrap.className = "dashboard-login-progress-step-status";
        item.appendChild(statusWrap);
      }

      if (
        labelNode &&
        statusWrap &&
        !item.querySelector(".dashboard-login-progress-step-separator")
      ) {
        var sep = document.createElement("span");
        sep.className = "dashboard-login-progress-step-separator";
        sep.setAttribute("aria-hidden", "true");
        sep.textContent = "\u00A0—\u00A0";
        labelNode.insertAdjacentElement("afterend", sep);
      }

      var textNode = statusWrap.querySelector("[data-step-status-text]");
      var initialText = "";
      if (textNode) {
        initialText = normalizeText(textNode.textContent);
      } else {
        initialText = stripStatusPrefix(statusWrap.textContent);
        textNode = document.createElement("span");
        textNode.setAttribute("data-step-status-text", "");
      }

      var prefixNode = document.createElement("span");
      prefixNode.className = "dashboard-login-progress-step-status-prefix";
      prefixNode.textContent = "Статус:\u00A0";

      statusWrap.textContent = "";
      statusWrap.appendChild(prefixNode);
      statusWrap.appendChild(textNode);

      if (initialText) {
        textNode.textContent = initialText;
      }

      return {
        labelNode: labelNode,
        statusWrap: statusWrap,
        textNode: textNode,
      };
    }

    function normalizeStepsMarkup() {
      if (!stepsEl) {
        return;
      }
      var items = stepsEl.querySelectorAll(".dashboard-login-progress-step");
      Array.prototype.forEach.call(items, function (item) {
        var refs = ensureStepMarkup(item);
        var stepLabel = normalizeText(item.getAttribute("data-step-label"));
        if (!stepLabel) {
          stepLabel = normalizeText(
            refs.labelNode ? refs.labelNode.textContent : ""
          );
        }
        var statusText = normalizeText(refs.textNode ? refs.textNode.textContent : "");
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
        var id = normalizeText(step && step.id);
        if (!id) {
          return;
        }
        var item = stepsEl.querySelector('[data-step-id="' + id + '"]');
        if (!item) {
          return;
        }
        var state = normalizeText(step.status) || "pending";
        item.setAttribute("data-step-status", state);
        var refs = ensureStepMarkup(item);
        var labelNode = refs.labelNode;
        var textNode = refs.textNode;

        var stepLabel = normalizeText(item.getAttribute("data-step-label"));
        if (!stepLabel) {
          stepLabel = normalizeText(labelNode ? labelNode.textContent : "");
        }
        var statusText = stepStatusText(step);
        var ariaStatusText = stepAriaStatusText(step);
        if (textNode) {
          textNode.textContent = statusText;
        }
        if (stepLabel && ariaStatusText) {
          item.setAttribute("aria-label", stepLabel + ": " + ariaStatusText);
        }
      });
    }

    function refreshOverviewValues() {
      if (!overviewContainer) {
        return;
      }
      var overviewUrl = overviewContainer.getAttribute("data-overview-url");
      if (!overviewUrl) {
        return;
      }
      fetch(overviewUrl, {
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
          var nodes = document.querySelectorAll("[data-overview-value]");
          Array.prototype.forEach.call(nodes, function (el) {
            var path = el.getAttribute("data-overview-value");
            var raw = getValueByPath(data, path);
            var formatted = formatValue(path || "", raw);
            if (el.textContent !== formatted) {
              el.textContent = formatted;
            }
          });
          if (overviewUpdated && data.updated_at) {
            overviewUpdated.textContent = data.updated_at;
          }
        })
        .catch(function () {
          return null;
        });
    }

    function applyPayload(payload) {
      if (!payload || typeof payload !== "object") {
        return;
      }

      updateSteps(payload.steps || []);

      if (payload.error) {
        done = true;
        failed = true;
        setStateClass("alert-warning");
        setMessage(payload.error);
        setRetryVisible(true);
        announce("Ошибка фоновой загрузки данных. Доступна повторная попытка.");
        return;
      }

      if (payload.done) {
        done = true;
        failed = false;
        setStateClass("alert-success");
        setMessage("Подготовка данных завершена. Обновляем обзор.");
        setRetryVisible(false);
        announce("Фоновая подготовка данных завершена. Обновляем обзор.");
        refreshOverviewValues();
        reloadOverviewOnce();
        return;
      }

      setStateClass("alert-info");
      setRetryVisible(false);
      setMessage(payload.message || "Фоновая загрузка данных продолжается.");
    }

    function scheduleNext(delayMs) {
      window.setTimeout(poll, delayMs);
    }

    function poll() {
      if (done || failed) {
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
        .then(function (payload) {
          applyPayload(payload);
          if (!done && !failed) {
            scheduleNext(1000);
          }
        })
        .catch(function () {
          if (!done && !failed) {
            setMessage(
              "Не удалось получить статус фоновой загрузки. Повторяем попытку."
            );
            scheduleNext(2500);
          }
        });
    }

    normalizeStepsMarkup();

    if (done && !failed) {
      setStateClass("alert-success");
      setRetryVisible(false);
      refreshOverviewValues();
      reloadOverviewOnce();
      return;
    }

    if (!done && !failed) {
      poll();
    }
  });
})();
