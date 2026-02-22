// Small UI helpers shared across the dashboard.
(function () {
  "use strict";

  function onReady(fn) {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", fn, { once: true });
    } else {
      fn();
    }
  }

  function getFocusableElements(root) {
    if (!root) {
      return [];
    }
    var selectors =
      "a[href],button:not([disabled]),input:not([disabled]):not([type='hidden']),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex='-1'])";
    return Array.prototype.slice.call(root.querySelectorAll(selectors));
  }

  function toPromise(value) {
    if (value && typeof value.then === "function") {
      return value;
    }
    return Promise.resolve(value);
  }

  window.panelConfirm = function (message) {
    return toPromise(window.confirm(message));
  };

  window.panelConfirmSubmit = function (targetOrEvent, message, options) {
    var form = null;
    if (targetOrEvent) {
      if (typeof targetOrEvent.preventDefault === "function") {
        targetOrEvent.preventDefault();
        form = targetOrEvent.target;
      } else if (targetOrEvent.tagName === "FORM") {
        form = targetOrEvent;
      }
    }
    if (!form && window.event && window.event.target) {
      if (typeof window.event.preventDefault === "function") {
        window.event.preventDefault();
      }
      form = window.event.target;
    }
    if (!form || typeof form.submit !== "function") {
      return false;
    }

    window
      .panelConfirm(message, options)
      .then(function (accepted) {
        if (accepted) {
          form.submit();
        }
      })
      .catch(function () {
        if (window.confirm(message)) {
          form.submit();
        }
      });

    return false;
  };

  onReady(function () {
    var modal = document.getElementById("confirm-modal");
    if (!modal) {
      return;
    }
    modal.setAttribute("aria-hidden", "true");
    if (!modal.hasAttribute("tabindex")) {
      modal.setAttribute("tabindex", "-1");
    }

    var titleEl =
      modal.querySelector("[data-confirm-title]") ||
      modal.querySelector("#confirm-title");
    var messageEl =
      modal.querySelector("[data-confirm-message]") ||
      modal.querySelector("#confirm-message");
    var cancelBtn = modal.querySelector("[data-confirm-cancel]");
    var confirmBtn = modal.querySelector("[data-confirm-ok]");

    var isOpen = false;
    var resolver = null;
    var rejecter = null;
    var previousFocus = null;

    function setOpen(state) {
      modal.hidden = !state;
      document.body.classList.toggle("modal-open", state);
      isOpen = state;
    }

    function cleanup() {
      resolver = null;
      rejecter = null;
      if (previousFocus && typeof previousFocus.focus === "function") {
        previousFocus.focus();
      }
      previousFocus = null;
      setOpen(false);
      modal.setAttribute("aria-hidden", "true");
    }

    function resolve(value) {
      if (resolver) {
        resolver(value);
      }
      cleanup();
    }

    function cancel() {
      resolve(false);
    }

    function confirm() {
      resolve(true);
    }

    function onKeyDown(event) {
      if (!isOpen) {
        return;
      }
      if (event.key === "Escape") {
        event.preventDefault();
        cancel();
        return;
      }
      if (event.key === "Tab") {
        var focusable = getFocusableElements(modal);
        if (!focusable.length) {
          event.preventDefault();
          return;
        }
        var currentIndex = focusable.indexOf(document.activeElement);
        if (event.shiftKey) {
          if (currentIndex <= 0) {
            focusable[focusable.length - 1].focus();
            event.preventDefault();
          }
        } else if (currentIndex === focusable.length - 1) {
          focusable[0].focus();
          event.preventDefault();
        }
      }
    }

    function onBackdropClick(event) {
      if (!isOpen) {
        return;
      }
      if (event.target === modal) {
        cancel();
      }
    }

    if (cancelBtn) {
      cancelBtn.addEventListener("click", function (event) {
        event.preventDefault();
        cancel();
      });
    }

    if (confirmBtn) {
      confirmBtn.addEventListener("click", function (event) {
        event.preventDefault();
        confirm();
      });
    }

    modal.addEventListener("keydown", onKeyDown);
    modal.addEventListener("click", onBackdropClick);

    document.addEventListener("submit", function (event) {
      var form = event.target;
      if (!form || typeof form.getAttribute !== "function") {
        return;
      }
      var message = form.getAttribute("data-confirm-message");
      if (!message) {
        return;
      }
      event.preventDefault();
      var dangerAttr = form.getAttribute("data-confirm-danger");
      var titleAttr = form.getAttribute("data-confirm-title");
      var danger =
        dangerAttr === "true" ||
        dangerAttr === "1" ||
        dangerAttr === "yes" ||
        dangerAttr === "on";

      window
        .panelConfirm(message, { danger: danger, title: titleAttr || undefined })
        .then(function (accepted) {
          if (accepted) {
            form.submit();
          }
        })
        .catch(function () {
          if (window.confirm(message)) {
            form.submit();
          }
        });
    });

    window.panelConfirm = function (message, options) {
      options = options || {};
      if (!modal || !confirmBtn) {
        return toPromise(window.confirm(message));
      }

      if (resolver) {
        rejecter = rejecter || function () {};
        rejecter(new Error("Confirmation already open."));
      }

      return new Promise(function (resolvePromise, rejectPromise) {
        resolver = resolvePromise;
        rejecter = rejectPromise;
        previousFocus = document.activeElement;

        if (titleEl) {
          var defaultTitle = options.danger
            ? "Опасное действие"
            : "Подтверждение действия";
          titleEl.textContent = options.title || defaultTitle;
        }
        if (messageEl) {
          messageEl.textContent = message || "";
        }

        confirmBtn.classList.toggle("danger", !!options.danger);

        setOpen(true);
        modal.setAttribute("aria-hidden", "false");
        modal.focus();

        var focusTarget = cancelBtn || confirmBtn;
        window.setTimeout(function () {
          if (focusTarget) {
            focusTarget.focus();
          }
        }, 0);
      });
    };

  });

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

  function setupOverviewRefresh() {
    var container = document.querySelector("[data-overview-url]");
    if (!container) {
      return;
    }
    var url = container.getAttribute("data-overview-url");
    var refreshRaw = container.getAttribute("data-overview-refresh");
    var refreshSeconds = parseInt(refreshRaw, 10);
    if (!url || !Number.isFinite(refreshSeconds) || refreshSeconds <= 0) {
      return;
    }
    refreshSeconds = Math.max(2, Math.min(refreshSeconds, 120));

    var liveEl = document.getElementById("overview-live");
    var updatedEl = document.querySelector("[data-overview-updated]");
    var hadError = false;
    var isFetching = false;

    function applyValues(data) {
      if (!data) {
        return;
      }
      var nodes = document.querySelectorAll("[data-overview-value]");
      Array.prototype.forEach.call(nodes, function (el) {
        var path = el.getAttribute("data-overview-value");
        var raw = getValueByPath(data, path);
        var formatted = formatValue(path || "", raw);
        if (el.textContent !== formatted) {
          el.textContent = formatted;
        }
      });
      if (updatedEl && data.updated_at) {
        updatedEl.textContent = data.updated_at;
        updatedEl.setAttribute(
          "aria-label",
          "Время последнего обновления: " + data.updated_at
        );
      }
    }

    function setBusy(state) {
      container.setAttribute("aria-busy", state ? "true" : "false");
    }

    function scheduleNext(delayMs) {
      window.setTimeout(poll, delayMs);
    }

    function poll() {
      if (document.hidden) {
        scheduleNext(refreshSeconds * 1000);
        return;
      }
      if (isFetching) {
        scheduleNext(refreshSeconds * 1000);
        return;
      }
      isFetching = true;
      setBusy(true);
      fetch(url, {
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
          isFetching = false;
          setBusy(false);
          applyValues(data);
          if (hadError && liveEl) {
            liveEl.textContent = "";
          }
          hadError = false;
          scheduleNext(refreshSeconds * 1000);
        })
        .catch(function () {
          isFetching = false;
          setBusy(false);
          if (liveEl) {
            liveEl.textContent =
              "Не удалось обновить данные обзора. Повторная попытка позже.";
          }
          hadError = true;
          scheduleNext(Math.max(refreshSeconds * 1000, 3000));
        });
    }

    poll();
  }

  function setupPermissionBuilder() {
    var builder = document.querySelector("[data-perm-builder]");
    if (!builder) {
      return;
    }

    var status = builder.querySelector(".perm-builder-status");

    function announce(message) {
      if (status && message) {
        status.textContent = message;
      }
    }

    function getBasePermission(viewEl) {
      if (!viewEl) {
        return null;
      }
      var canList = viewEl.querySelector('input[data-perm-name="can_list"]');
      if (canList) {
        return canList;
      }
      return viewEl.querySelector('input[data-perm-base="1"]');
    }

    function syncCategory(categoryEl, trigger) {
      if (!categoryEl) {
        return false;
      }
      var categoryMenu = categoryEl.querySelector(
        'input[data-perm-name="menu_access"][data-perm-scope="category"]'
      );
      if (trigger && trigger === categoryMenu && categoryMenu && !categoryMenu.checked) {
        var inputs = categoryEl.querySelectorAll('input[data-perm-name]');
        var changed = false;
        for (var i = 0; i < inputs.length; i += 1) {
          var input = inputs[i];
          if (input === categoryMenu) {
            continue;
          }
          if (input.checked) {
            input.checked = false;
            changed = true;
          }
        }
        if (changed) {
          announce("Доступы в категории отключены вместе с меню.");
        }
        return changed;
      }
      return false;
    }

    function syncView(viewEl, trigger) {
      if (!viewEl) {
        return false;
      }
      var viewMenu = viewEl.querySelector(
        'input[data-perm-name="menu_access"][data-perm-scope="view"]'
      );
      var categoryEl = viewEl.closest(".perm-category");
      var categoryMenu = categoryEl
        ? categoryEl.querySelector(
            'input[data-perm-name="menu_access"][data-perm-scope="category"]'
          )
        : null;
      var permInputs = viewEl.querySelectorAll(
        'input[data-perm-name]:not([data-perm-name="menu_access"])'
      );
      var base = getBasePermission(viewEl);
      var changed = false;

      if (trigger && trigger === viewMenu && viewMenu && !viewMenu.checked) {
        for (var i = 0; i < permInputs.length; i += 1) {
          if (permInputs[i].checked) {
            permInputs[i].checked = false;
            changed = true;
          }
        }
        if (changed) {
          announce("Права раздела сняты вместе с пунктом меню.");
        }
        return changed;
      }

      if (trigger && base && trigger === base && !base.checked) {
        var requires = viewEl.querySelectorAll('input[data-perm-requires-base="1"]');
        for (var r = 0; r < requires.length; r += 1) {
          if (requires[r].checked) {
            requires[r].checked = false;
            changed = true;
          }
        }
        if (changed) {
          announce("Права действий отключены без доступа к просмотру.");
        }
        return changed;
      }

      if (
        trigger &&
        trigger.dataset &&
        trigger.dataset.permRequiresBase === "1" &&
        trigger.checked
      ) {
        if (base && !base.checked) {
          base.checked = true;
          changed = true;
        }
      }

      var anyChecked = false;
      for (var j = 0; j < permInputs.length; j += 1) {
        if (permInputs[j].checked) {
          anyChecked = true;
          break;
        }
      }
      if (anyChecked) {
        if (viewMenu && !viewMenu.checked) {
          viewMenu.checked = true;
          changed = true;
        }
        if (categoryMenu && !categoryMenu.checked) {
          categoryMenu.checked = true;
          changed = true;
        }
      }

      if (changed) {
        announce("Права автоматически согласованы.");
      }
      return changed;
    }

    builder.addEventListener("change", function (event) {
      var target = event.target;
      if (!target || target.type !== "checkbox") {
        return;
      }
      if (!target.dataset || !target.dataset.permName) {
        return;
      }
      var viewEl = target.closest(".perm-view");
      var categoryEl = target.closest(".perm-category");
      var changed = false;
      if (categoryEl) {
        changed = syncCategory(categoryEl, target) || changed;
      }
      if (viewEl) {
        changed = syncView(viewEl, target) || changed;
      }
      if (changed && status) {
        window.setTimeout(function () {
          status.textContent = "";
        }, 2000);
      }
    });
  }

  function setupRolePermissionToggles() {
    var toggles = document.querySelectorAll("[data-role-permissions-toggle]");
    if (!toggles.length) {
      return;
    }

    function getText(toggle, expanded) {
      var expandedText =
        toggle.getAttribute("data-expanded-label") || "Свернуть права";
      var collapsedText =
        toggle.getAttribute("data-collapsed-label") || "Развернуть права";
      return expanded ? expandedText : collapsedText;
    }

    function setExpanded(toggle, expanded) {
      var panelId = toggle.getAttribute("aria-controls");
      var panel = panelId ? document.getElementById(panelId) : null;
      toggle.setAttribute("aria-expanded", expanded ? "true" : "false");
      toggle.textContent = getText(toggle, expanded);
      if (panel) {
        panel.hidden = !expanded;
        panel.classList.toggle("hidden", !expanded);
      }
    }

    function activate(event) {
      if (event.type === "keydown") {
        var key = event.key || "";
        var isEnter = key === "Enter";
        var isSpace = key === " " || key === "Space" || key === "Spacebar";
        if (!isEnter && !isSpace) {
          return;
        }
        event.preventDefault();
      } else if (event.type === "click") {
        event.preventDefault();
      } else {
        return;
      }

      var toggle = event.currentTarget;
      var expanded = toggle.getAttribute("aria-expanded") === "true";
      setExpanded(toggle, !expanded);
    }

    Array.prototype.forEach.call(toggles, function (toggle) {
      if (toggle.getAttribute("data-role-permissions-bound") === "1") {
        return;
      }
      toggle.setAttribute("data-role-permissions-bound", "1");
      setExpanded(toggle, toggle.getAttribute("aria-expanded") === "true");
      toggle.addEventListener("click", activate);
      toggle.addEventListener("keydown", activate);
    });
  }

  function setupHeaderNotificationHub() {
    var stack = document.querySelector("[data-header-notify-url]");
    if (!stack) {
      return;
    }

    var pollUrl = stack.getAttribute("data-header-notify-url");
    if (!pollUrl) {
      return;
    }

    var csrfToken = stack.getAttribute("data-header-notify-csrf") || "";
    var systemLink = document.querySelector("[data-system-link]");
    var systemBadge = document.querySelector("[data-system-badge]");
    var messengerBadge = document.querySelector("[data-messenger-badge]");
    var communicationsBadge = document.querySelector("[data-communications-badge]");
    var messengerLink = document.querySelector("[data-messenger-link]");
    var communicationsLink = document.querySelector("[data-communications-link]");
    var systemUnreadText = document.querySelector("[data-system-unread-text]");
    var systemHistoryList = document.querySelector("[data-system-history-list]");
    var systemHistoryEmpty = document.querySelector("[data-system-history-empty]");
    var systemHistoryWrapper = document.querySelector("[data-system-history-wrapper]");
    var systemMarkReadButtons = Array.prototype.slice.call(
      document.querySelectorAll("[data-system-mark-read]")
    );
    var systemClearButtons = Array.prototype.slice.call(
      document.querySelectorAll("[data-system-clear]")
    );
    var liveNode = document.querySelector("[data-header-notify-live]");
    var systemLabel = "Уведомления AutoCraft";
    var systemUnreadPrefix = "Непрочитанных уведомлений AutoCraft: ";

    var previous = {
      system: parseInt(String((systemBadge && systemBadge.textContent) || "").replace(/[^\d]/g, ""), 10) || 0,
      messenger: parseInt(String((messengerBadge && messengerBadge.textContent) || "").replace(/[^\d]/g, ""), 10) || 0,
      communications: parseInt(String((communicationsBadge && communicationsBadge.textContent) || "").replace(/[^\d]/g, ""), 10) || 0,
    };

    var shown = Object.create(null);
    var inFlight = false;
    var audioContext = null;
    var audioEnabled = false;
    var toastQueue = [];
    var activeToasts = 0;
    var maxVisibleToasts = 4;

    function announce(text) {
      if (liveNode) {
        liveNode.textContent = text || "";
      }
    }

    function updateSystemUnreadText(value) {
      if (systemUnreadText) {
        systemUnreadText.textContent = systemUnreadPrefix + String(value);
      }
    }

    function getActionUrl(buttons, attrName) {
      for (var i = 0; i < buttons.length; i += 1) {
        var button = buttons[i];
        if (!button || typeof button.getAttribute !== "function") {
          continue;
        }
        var url = button.getAttribute(attrName) || "";
        if (url) {
          return url;
        }
      }
      return "";
    }

    function ensureAudioEnabled() {
      if (audioEnabled) {
        return;
      }
      try {
        var Ctx = window.AudioContext || window.webkitAudioContext;
        if (!Ctx) {
          return;
        }
        audioContext = audioContext || new Ctx();
        if (audioContext.state === "suspended") {
          audioContext.resume();
        }
        audioEnabled = true;
      } catch (_e) {
        audioEnabled = false;
      }
    }

    function playNotificationSound() {
      if (!audioEnabled || !audioContext) {
        return;
      }
      try {
        var now = audioContext.currentTime;
        var osc = audioContext.createOscillator();
        var gain = audioContext.createGain();
        osc.type = "triangle";
        osc.frequency.setValueAtTime(940, now);
        gain.gain.setValueAtTime(0.0001, now);
        gain.gain.exponentialRampToValueAtTime(0.07, now + 0.02);
        gain.gain.exponentialRampToValueAtTime(0.0001, now + 0.16);
        osc.connect(gain);
        gain.connect(audioContext.destination);
        osc.start(now);
        osc.stop(now + 0.17);
      } catch (_e) {
        // Silent fallback.
      }
    }

    function pulseBadge(node) {
      if (!node) {
        return;
      }
      node.classList.add("pulse");
      window.setTimeout(function () {
        node.classList.remove("pulse");
      }, 1200);
    }

    function buildUnreadAriaLabel(labelPrefix, value) {
      return (labelPrefix || "Уведомления") + ": " + String(value) + " непрочитанных";
    }

    function setBadge(node, count, labelPrefix, options) {
      if (!node) {
        return;
      }
      options = options || {};
      var linkNode = options.linkNode || null;
      var value = Number(count || 0);
      if (!Number.isFinite(value) || value < 0) {
        value = 0;
      }
      var countStyle = String(node.getAttribute("data-count-style") || "").toLowerCase();
      var ariaLabel = buildUnreadAriaLabel(labelPrefix, value);

      if (countStyle === "text") {
        node.hidden = false;
        node.textContent = String(value);
        node.classList.toggle("is-zero", value <= 0);
        node.setAttribute("aria-label", ariaLabel);
        if (linkNode) {
          linkNode.setAttribute("aria-label", ariaLabel);
          linkNode.setAttribute("title", ariaLabel);
        }
        return;
      }

      node.classList.toggle("is-zero", value <= 0);
      if (value > 0) {
        node.hidden = false;
        node.textContent = "+" + value;
      } else {
        node.hidden = true;
        node.textContent = "+0";
      }
      node.setAttribute("aria-label", ariaLabel);
    }

    function removeToast(node) {
      if (!node || !node.parentNode) {
        return;
      }
      if (node.getAttribute("data-toast-active") === "1") {
        activeToasts = Math.max(0, activeToasts - 1);
        node.setAttribute("data-toast-active", "0");
      }
      node.parentNode.removeChild(node);
      flushToastQueue();
    }

    function enqueueToast(item) {
      toastQueue.push(item);
      flushToastQueue();
    }

    function flushToastQueue() {
      while (activeToasts < maxVisibleToasts && toastQueue.length > 0) {
        renderToast(toastQueue.shift());
      }
    }

    function getActiveSectionKind() {
      var path = String((window.location && window.location.pathname) || "").toLowerCase();
      if (path.indexOf("/messenger") === 0) {
        return "messenger";
      }
      if (path.indexOf("/communications") === 0) {
        return "communications";
      }
      if (path.indexOf("/notify-center") === 0) {
        return "system";
      }
      return "";
    }

    function getToastLifetimeMs(itemKind) {
      var activeKind = getActiveSectionKind();
      if (activeKind && itemKind && activeKind === itemKind) {
        return 5000;
      }
      return 20000;
    }

    function renderToast(item) {
      if (!stack) {
        return;
      }
      var kind = String((item && item.kind) || "").toLowerCase();
      var level = String((item && item.level) || "").toLowerCase();

      var toast = document.createElement("section");
      toast.className = "admin-toast";
      if (kind) {
        toast.classList.add("kind-" + kind);
      }
      if (level) {
        toast.classList.add("level-" + level);
      }
      toast.setAttribute("role", "status");
      toast.setAttribute("aria-live", "polite");
      toast.setAttribute("data-toast-active", "1");
      activeToasts += 1;

      var header = document.createElement("div");
      header.className = "admin-toast-header";

      var title = document.createElement("div");
      title.className = "admin-toast-title";
      title.textContent = item.title || item.subject || "Уведомление";

      var close = document.createElement("button");
      close.type = "button";
      close.className = "admin-toast-close";
      close.setAttribute("aria-label", "Закрыть уведомление");
      close.textContent = "\u00D7";
      close.addEventListener("click", function () {
        removeToast(toast);
      });

      header.appendChild(title);
      header.appendChild(close);
      toast.appendChild(header);

      var body = document.createElement("p");
      body.className = "admin-toast-body";
      body.textContent = item.body || "";
      toast.appendChild(body);

      var meta = document.createElement("div");
      meta.className = "admin-toast-meta";
      var kindMap = {
        system: "AutoCraft",
        messenger: "Мессенджер",
        communications: "Центр коммуникаций",
      };
      var source = item.source_label || kindMap[kind] || "";
      var parts = [];
      if (item.created_at) {
        parts.push(item.created_at);
      }
      if (source) {
        parts.push(source);
      }
      meta.textContent = parts.join(" • ");
      toast.appendChild(meta);

      var actions = document.createElement("div");
      actions.className = "admin-toast-actions";
      if (item.url) {
        var go = document.createElement("a");
        go.className = "btn admin-toast-btn primary";
        go.href = item.url;
        go.textContent = item.action_label || "Перейти";
        go.addEventListener("click", function () {
          removeToast(toast);
        });
        actions.appendChild(go);
      }

      var closeBtn = document.createElement("button");
      closeBtn.type = "button";
      closeBtn.className = "btn admin-toast-btn";
      closeBtn.textContent = "Закрыть";
      closeBtn.addEventListener("click", function () {
        removeToast(toast);
      });
      actions.appendChild(closeBtn);
      toast.appendChild(actions);

      stack.appendChild(toast);
      var timeoutMs = getToastLifetimeMs(kind);
      window.setTimeout(function () {
        removeToast(toast);
      }, timeoutMs);
    }

    function showBanners(items) {
      if (!Array.isArray(items) || !items.length) {
        return;
      }
      for (var i = 0; i < items.length; i += 1) {
        var item = items[i] || {};
        var keyParts = [
          item.kind || "",
          item.id || "",
          item.created_at || "",
          item.title || "",
          item.subject || "",
        ];
        var key = "hub:" + keyParts.join("|");
        if (shown[key]) {
          continue;
        }
        shown[key] = true;
        enqueueToast(item);
      }
    }

    function renderSystemHistory(items) {
      if (!systemHistoryList || !systemHistoryEmpty || !systemHistoryWrapper) {
        return;
      }
      var list = Array.isArray(items) ? items : [];
      systemHistoryList.innerHTML = "";
      if (!list.length) {
        systemHistoryWrapper.hidden = true;
        systemHistoryEmpty.hidden = false;
        return;
      }
      systemHistoryEmpty.hidden = true;
      systemHistoryWrapper.hidden = false;
      for (var i = 0; i < list.length; i += 1) {
        var item = list[i] || {};
        var li = document.createElement("li");
        li.className = "panel-system-history-item";

        var title = document.createElement("div");
        title.className = "panel-system-history-item-title";
        title.textContent = item.title || "Уведомление AutoCraft";
        li.appendChild(title);

        var body = document.createElement("div");
        body.className = "panel-system-history-item-body";
        body.textContent = item.body || "";
        li.appendChild(body);

        var meta = document.createElement("div");
        meta.className = "panel-system-history-item-meta";
        meta.textContent = item.created_at || "";
        li.appendChild(meta);

        systemHistoryList.appendChild(li);
      }
    }

    function applyCounters(counters) {
      counters = counters || {};
      var next = {
        system: Number(counters.system || 0),
        messenger: Number(counters.messenger || 0),
        communications: Number(counters.communications || 0),
      };
      var hasIncrease = false;

      if (next.system > previous.system) {
        pulseBadge(systemBadge);
        hasIncrease = true;
      }
      if (next.messenger > previous.messenger) {
        pulseBadge(messengerBadge);
        hasIncrease = true;
      }
      if (next.communications > previous.communications) {
        pulseBadge(communicationsBadge);
        hasIncrease = true;
      }

      setBadge(systemBadge, next.system, systemLabel, { linkNode: systemLink });
      setBadge(messengerBadge, next.messenger, "Мессенджер", { linkNode: messengerLink });
      setBadge(communicationsBadge, next.communications, "Центр коммуникаций", {
        linkNode: communicationsLink,
      });

      updateSystemUnreadText(next.system);

      if (hasIncrease) {
        playNotificationSound();
        announce("Получены новые уведомления.");
      }

      previous = next;
    }

    function markSystemRead() {
      if (!systemMarkReadButtons.length) {
        return;
      }
      var readUrl = getActionUrl(systemMarkReadButtons, "data-system-mark-read-url");
      if (!readUrl || !csrfToken) {
        return;
      }

      fetch(readUrl, {
        method: "POST",
        headers: {
          Accept: "application/json",
          "X-CSRFToken": csrfToken,
        },
        credentials: "same-origin",
        cache: "no-store",
      })
        .then(function (resp) {
          return resp.json().then(function (data) {
            if (!resp.ok || !data || data.ok === false) {
              throw new Error((data && data.message) ? data.message : "Не удалось отметить уведомления.");
            }
            return data;
          });
        })
        .then(function () {
          setBadge(systemBadge, 0, systemLabel, { linkNode: systemLink });
          previous.system = 0;
          updateSystemUnreadText(0);
          announce("Уведомления AutoCraft отмечены как прочитанные.");
        })
        .catch(function (error) {
          announce((error && error.message) ? error.message : "Не удалось обновить статус уведомлений.");
        });
    }

    function clearSystemHistory() {
      if (!systemClearButtons.length) {
        return;
      }
      var clearUrl = getActionUrl(systemClearButtons, "data-system-clear-url");
      if (!clearUrl || !csrfToken) {
        return;
      }

      window
        .panelConfirm(
          "Очистить историю уведомлений AutoCraft? Записи останутся в базе, но исчезнут из вашего списка.",
          { danger: true, title: "Очистка истории" }
        )
        .then(function (accepted) {
          if (!accepted) {
            return;
          }
          return fetch(clearUrl, {
            method: "POST",
            headers: {
              Accept: "application/json",
              "X-CSRFToken": csrfToken,
            },
            credentials: "same-origin",
            cache: "no-store",
          })
            .then(function (resp) {
              return resp.json().then(function (data) {
                if (!resp.ok || !data || data.ok === false) {
                  throw new Error((data && data.message) ? data.message : "Не удалось очистить историю уведомлений.");
                }
                return data;
              });
            })
            .then(function (data) {
              renderSystemHistory((data && data.system_recent) || []);
              setBadge(systemBadge, 0, systemLabel, { linkNode: systemLink });
              previous.system = 0;
              updateSystemUnreadText(0);
              announce("История уведомлений AutoCraft очищена.");
              if (getActiveSectionKind() === "system") {
                window.setTimeout(function () {
                  window.location.reload();
                }, 120);
              }
            });
        })
        .catch(function (error) {
          announce((error && error.message) ? error.message : "Не удалось очистить историю уведомлений.");
        });
    }

    function poll() {
      if (inFlight || document.hidden) {
        return;
      }
      inFlight = true;
      fetch(pollUrl, {
        headers: { Accept: "application/json" },
        credentials: "same-origin",
        cache: "no-store",
      })
        .then(function (resp) {
          if (!resp.ok) {
            throw new Error("notify hub bad response");
          }
          return resp.json();
        })
        .then(function (data) {
          if (!data || data.ok === false) {
            return;
          }
          applyCounters(data.counters || {});
          renderSystemHistory(data.system_recent || []);
          showBanners(data.banners || []);
        })
        .catch(function () {
          // Silent fallback.
        })
        .finally(function () {
          inFlight = false;
        });
    }

    Array.prototype.forEach.call(systemMarkReadButtons, function (button) {
      button.addEventListener("click", function (event) {
        event.preventDefault();
        markSystemRead();
      });
    });

    Array.prototype.forEach.call(systemClearButtons, function (button) {
      button.addEventListener("click", function (event) {
        event.preventDefault();
        clearSystemHistory();
      });
    });

    ["click", "keydown", "touchstart"].forEach(function (name) {
      document.addEventListener(name, ensureAudioEnabled, { passive: true, once: true });
    });
    poll();
    window.setInterval(poll, 4000);
  }

  function setupAdminNotificationFeed() {
    var stack = document.querySelector("[data-admin-notifications-url]");
    if (!stack) {
      return;
    }
    var url = stack.getAttribute("data-admin-notifications-url");
    if (!url) {
      return;
    }
    var csrfToken = stack.getAttribute("data-admin-notifications-csrf") || "";
    if (!csrfToken) {
      return;
    }

    var shown = Object.create(null);
    var inFlight = false;
    var stopped = false;

    function removeToast(node) {
      if (!node || !node.parentNode) {
        return;
      }
      node.parentNode.removeChild(node);
    }

    function renderToast(item) {
      var toast = document.createElement("section");
      toast.className = "admin-toast";
      toast.setAttribute("role", "status");
      toast.setAttribute("aria-live", "polite");

      var header = document.createElement("div");
      header.className = "admin-toast-header";

      var title = document.createElement("div");
      title.className = "admin-toast-title";
      title.textContent = item.subject || "Сообщение администратора";

      var close = document.createElement("button");
      close.type = "button";
      close.className = "admin-toast-close";
      close.setAttribute("aria-label", "Закрыть уведомление");
      close.textContent = "\u00D7";
      close.addEventListener("click", function () {
        removeToast(toast);
      });

      header.appendChild(title);
      header.appendChild(close);
      toast.appendChild(header);

      var body = document.createElement("p");
      body.className = "admin-toast-body";
      body.textContent = item.body || "";
      toast.appendChild(body);

      var meta = document.createElement("div");
      meta.className = "admin-toast-meta";
      var author = item.author ? (" • " + item.author) : "";
      meta.textContent = (item.created_at || "") + author;
      toast.appendChild(meta);

      stack.appendChild(toast);
      while (stack.children.length > 5) {
        removeToast(stack.firstElementChild);
      }
      window.setTimeout(function () {
        removeToast(toast);
      }, 18000);
    }

    function showItems(items) {
      if (!Array.isArray(items) || !items.length) {
        return;
      }
      items.forEach(function (item) {
        var id = Number(item && item.id ? item.id : 0);
        if (id > 0 && shown[id]) {
          return;
        }
        if (id > 0) {
          shown[id] = true;
        }
        renderToast(item || {});
      });
    }

    function poll() {
      if (stopped || inFlight || document.hidden) {
        return;
      }
      inFlight = true;
      fetch(url, {
        method: "POST",
        headers: {
          Accept: "application/json",
          "X-CSRFToken": csrfToken,
        },
        credentials: "same-origin",
        cache: "no-store",
      })
        .then(function (resp) {
          if (resp.status === 401 || resp.status === 403) {
            stopped = true;
            return null;
          }
          if (!resp.ok) {
            throw new Error("admin notifications bad response");
          }
          return resp.json();
        })
        .then(function (data) {
          if (!data) {
            return;
          }
          showItems(data.items || []);
        })
        .catch(function () {
          // Silent fallback.
        })
        .finally(function () {
          inFlight = false;
        });
    }

    poll();
    window.setInterval(poll, 7000);
  }

  onReady(setupOverviewRefresh);
  onReady(setupPermissionBuilder);
  onReady(setupRolePermissionToggles);
  onReady(setupHeaderNotificationHub);
  onReady(setupAdminNotificationFeed);
})();
