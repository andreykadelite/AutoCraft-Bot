(function () {
  "use strict";

  function onReady(fn) {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", fn, { once: true });
      return;
    }
    fn();
  }

  function toArray(items) {
    return Array.prototype.slice.call(items || []);
  }

  function asBool(value, fallback) {
    if (value === true || value === 1) return true;
    if (value === false || value === 0) return false;
    if (value === null || value === undefined || value === "") return !!fallback;
    var text = String(value).trim().toLowerCase();
    if (text === "1" || text === "true" || text === "yes" || text === "on") return true;
    if (text === "0" || text === "false" || text === "no" || text === "off") return false;
    return !!fallback;
  }

  function grantStatusLabel(status) {
    var key = String(status || "idle").trim().toLowerCase();
    var map = {
      idle: "ожидание",
      pending: "ожидает подтверждения",
      approved: "доступ подтвержден",
      denied: "доступ отклонен",
      expired: "срок действия истек",
      cancelled: "запрос отменен",
      disabled: "отключено",
      manual_required: "требуется ручное подтверждение",
    };
    return map[key] || key || "ожидание";
  }

  function peerControlStatusLabel(status) {
    var key = String(status || "idle").trim().toLowerCase();
    var map = {
      idle: "не запрошен",
      pending: "ожидает ответа",
      approved: "одобрен",
      denied: "отклонен",
      expired: "истек",
      cancelled: "отменен",
      disabled: "отключено",
      manual_required: "требуется запрос",
      error: "ошибка",
    };
    return map[key] || key || "не запрошен";
  }

  function normalizePeerControlState(raw) {
    var source = raw && typeof raw === "object" ? raw : {};
    var status = String(source.status || "idle").trim().toLowerCase();
    if (
      ["idle", "pending", "approved", "denied", "expired", "cancelled", "disabled", "manual_required", "error"].indexOf(status) === -1
    ) {
      status = "idle";
    }
    var state = {
      status: status,
      message: String(source.message || "").trim(),
      request_id: String(source.request_id || "").trim(),
      grant_token: String(source.grant_token || "").trim(),
      grant_expires_at: String(source.grant_expires_at || "").trim(),
      grant_expires_epoch: Number(source.grant_expires_epoch || 0) || 0,
      updated_at: String(source.updated_at || "").trim(),
    };
    if (status !== "approved") {
      state.grant_token = "";
      state.grant_expires_at = "";
      state.grant_expires_epoch = 0;
    }
    return state;
  }

  function decisionLabel(decision) {
    var key = String(decision || "").trim().toLowerCase();
    if (key === "allow") return "разрешить";
    if (key === "deny") return "отклонить";
    if (key === "prompt") return "спрашивать каждый раз";
    return key;
  }

  function modeLabel(mode) {
    var key = String(mode || "multi").trim().toLowerCase();
    if (key === "server") return "Сервер";
    if (key === "client") return "Клиент";
    return "Комбинированный";
  }

  function setupLanServersPage() {
    var holder = document.querySelector("[data-lan-state-url]");
    if (!holder) return;

    var stateUrl = holder.getAttribute("data-lan-state-url");
    if (!stateUrl) return;

    var els = {
      form: document.getElementById("lan-settings-form"),
      runningChip: document.getElementById("lan-running-chip"),
      serviceToggleBtn: document.getElementById("lan-service-toggle-btn"),
      statusLine: document.getElementById("lan-status-line"),
      selectedLine: document.getElementById("lan-selected-line"),
      selectedGrant: document.getElementById("lan-selected-grant"),
      openSelected: document.getElementById("lan-open-selected"),
      logEl: document.getElementById("lan-log"),
      tableBody: document.getElementById("lan-peers-body"),
      requestsBody: document.getElementById("lan-requests-body"),
      policiesBody: document.getElementById("lan-policies-body"),
      requestCount: document.getElementById("lan-request-count"),
      managerCount: document.getElementById("lan-manager-count"),
      managerMeta: document.getElementById("lan-manager-meta"),
      managerFilter: document.getElementById("lan-manager-filter"),
      managerSort: document.getElementById("lan-manager-sort"),
      managerOrder: document.getElementById("lan-manager-order"),
      managerSearch: document.getElementById("lan-manager-search"),
      remoteRoleModeInputs: toArray(document.querySelectorAll('input[type="radio"][name="remote_role_mode"]')),
      liveRegion: document.getElementById("lan-live-region"),
      summaryServiceChip: document.getElementById("lan-summary-service-chip"),
      summaryMode: document.getElementById("lan-summary-mode"),
      summaryPeersChip: document.getElementById("lan-summary-peers-chip"),
      summaryPeers: document.getElementById("lan-summary-peers"),
      summaryMulticast: document.getElementById("lan-summary-multicast"),
      summaryPanel: document.getElementById("lan-summary-panel"),
      summaryRemote: document.getElementById("lan-summary-remote"),
      summaryRequests: document.getElementById("lan-summary-requests"),
      summaryErrorChip: document.getElementById("lan-summary-error-chip"),
      summaryError: document.getElementById("lan-summary-error"),
      debugStatusValue: document.getElementById("lan-debug-status-value"),
      debugPathValue: document.getElementById("lan-debug-path-value"),
      debugToggleBtn: document.getElementById("lan-debug-toggle-btn"),
      debugLevelField: document.getElementById("lan-debug-level"),
      debugMaxMbField: document.getElementById("lan-debug-max-mb"),
      debugBackupCountField: document.getElementById("lan-debug-backup-count"),
      tabButtons: toArray(document.querySelectorAll("[data-lan-tab-target]")),
      tabPanels: toArray(document.querySelectorAll("[data-lan-tab-panel]")),
      quickRequestAlert: document.getElementById("lan-quick-request-alert"),
      quickRequestCount: document.getElementById("lan-quick-request-count"),
      quickRequestMessage: document.getElementById("lan-quick-request-message"),
      quickRequestController: document.getElementById("lan-quick-request-controller"),
      quickRequestIp: document.getElementById("lan-quick-request-ip"),
      quickRequestUser: document.getElementById("lan-quick-request-user"),
      quickRequestRoles: document.getElementById("lan-quick-request-roles"),
      quickRequestCreated: document.getElementById("lan-quick-request-created"),
      quickOpenAccessBtn: document.querySelector("[data-lan-open-access-tab]"),
    };

    var state = {
      settingsSignature: holder.getAttribute("data-lan-settings-signature") || "",
      peersSignature: "",
      logsSignature: "",
      selectedNodeId: holder.getAttribute("data-lan-selected-node-id") || "",
      selectUrlTemplate: holder.getAttribute("data-lan-select-url-template") || "/servers/control/select/__NODE__",
      requestUrlTemplate: holder.getAttribute("data-lan-request-url-template") || "/servers/control/request/__NODE__",
      respondUrlTemplate: holder.getAttribute("data-lan-respond-url-template") || "/servers/control/request/__REQ__/respond",
      policyUrlTemplate: holder.getAttribute("data-lan-policy-url-template") || "/servers/control/policy/__NODE__",
      removePeerUrlTemplate: holder.getAttribute("data-lan-remove-peer-url-template") || "/servers/peer/remove/__NODE__",
      frontendDebugUrl: holder.getAttribute("data-lan-frontend-debug-url") || "",
      csrfToken: holder.getAttribute("data-lan-csrf") || "",
      canAction: asBool(holder.getAttribute("data-lan-can-action"), false),
      pollingInFlight: false,
      peersByNodeId: Object.create(null),
      peersList: [],
      policiesByNodeId: Object.create(null),
      peerControlByNodeId: Object.create(null),
      grantState: { status: "idle", message: "" },
      lastLiveAnnouncement: "",
      lastSummarySnapshot: "",
      debugEnabled: false,
      debugLevel: "MAX",
      frontendDebugBound: false,
      frontendConsolePatched: false,
      storageKey: "lan_servers_active_tab",
      lastQuickRequestId: "",
    };

    function postJson(url, payload) {
      return fetch(url, {
        method: "POST",
        credentials: "same-origin",
        cache: "no-store",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
          "X-CSRFToken": state.csrfToken,
        },
        body: JSON.stringify(payload || {}),
      }).then(function (response) {
        return response
          .json()
          .catch(function () {
            return {};
          })
          .then(function (data) {
            if (!response.ok || data.ok === false) {
              var msg = String((data && data.message) || ("HTTP " + response.status));
              throw new Error(msg);
            }
            return data || {};
          });
      });
    }

    function announceLive(text, force) {
      if (!els.liveRegion) return;
      var next = String(text || "").trim();
      if (!next) return;
      if (!force && next === state.lastLiveAnnouncement) return;
      els.liveRegion.textContent = next;
      state.lastLiveAnnouncement = next;
    }

    function setStatusText(text) {
      if (!els.statusLine || !text) return;
      var next = String(text);
      if (els.statusLine.textContent !== next) {
        els.statusLine.textContent = next;
      }
      announceLive(next, false);
    }

    function setChipState(chip, level, text) {
      if (!chip) return;
      chip.classList.remove("ok", "warn", "bad");
      chip.classList.add(level === "bad" ? "bad" : level === "warn" ? "warn" : "ok");
      if (typeof text !== "undefined" && text !== null) {
        chip.textContent = String(text);
      }
    }

    function setStateClass(el, level) {
      if (!el) return;
      el.classList.remove("lan-status-ok", "lan-status-warn", "lan-status-bad");
      el.classList.add(level === "bad" ? "lan-status-bad" : level === "warn" ? "lan-status-warn" : "lan-status-ok");
    }

    function updateServiceToggleButton(running) {
      if (!els.serviceToggleBtn) return;
      var startLabel = String(els.serviceToggleBtn.getAttribute("data-start-label") || "Запустить службу");
      var stopLabel = String(els.serviceToggleBtn.getAttribute("data-stop-label") || "Остановить службу");
      var startValue = String(els.serviceToggleBtn.getAttribute("data-start-value") || "start");
      var stopValue = String(els.serviceToggleBtn.getAttribute("data-stop-value") || "stop");
      els.serviceToggleBtn.value = running ? stopValue : startValue;
      els.serviceToggleBtn.textContent = running ? stopLabel : startLabel;
      els.serviceToggleBtn.classList.toggle("btn-primary", !running);
      els.serviceToggleBtn.classList.toggle("btn", running);
      els.serviceToggleBtn.classList.toggle("danger", running);
    }

    function updateDebugLogging(debugCfg) {
      var cfg = debugCfg || {};
      var enabled = asBool(cfg.enabled, false);
      state.debugEnabled = enabled;
      state.debugLevel = String(cfg.level || state.debugLevel || "MAX").toUpperCase();
      if (els.debugStatusValue) {
        var statusText =
          typeof cfg.status_text === "string" && cfg.status_text
            ? cfg.status_text
            : enabled
            ? "включен"
            : "выключен";
        els.debugStatusValue.textContent = statusText;
      }
      if (els.debugPathValue && typeof cfg.path === "string" && cfg.path) {
        els.debugPathValue.textContent = cfg.path;
      }
      if (els.debugLevelField && typeof cfg.level === "string" && cfg.level) {
        els.debugLevelField.value = cfg.level;
      }
      if (els.debugMaxMbField && Number.isFinite(Number(cfg.max_mb))) {
        els.debugMaxMbField.value = String(cfg.max_mb);
      }
      if (els.debugBackupCountField && Number.isFinite(Number(cfg.backup_count))) {
        els.debugBackupCountField.value = String(cfg.backup_count);
      }
      if (els.debugToggleBtn) {
        var enableLabel = String(els.debugToggleBtn.getAttribute("data-debug-enable-label") || "Включить debug");
        var disableLabel = String(els.debugToggleBtn.getAttribute("data-debug-disable-label") || "Выключить debug");
        els.debugToggleBtn.textContent = enabled ? disableLabel : enableLabel;
        els.debugToggleBtn.classList.toggle("btn-primary", !enabled);
        els.debugToggleBtn.classList.toggle("btn", enabled);
        els.debugToggleBtn.classList.toggle("danger", enabled);
      }
    }

    function postFrontendDebug(payload) {
      if (!state.debugEnabled || !state.frontendDebugUrl || !state.csrfToken) {
        return;
      }
      fetch(state.frontendDebugUrl, {
        method: "POST",
        credentials: "same-origin",
        cache: "no-store",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
          "X-CSRFToken": state.csrfToken,
        },
        body: JSON.stringify(payload || {}),
      }).catch(function () {});
    }

    function safeFrontendText(value) {
      if (value === null || typeof value === "undefined") {
        return "";
      }
      if (typeof value === "string") {
        return value;
      }
      if (typeof value === "number" || typeof value === "boolean") {
        return String(value);
      }
      if (value instanceof Error) {
        return String(value.message || value.name || "Error");
      }
      try {
        return JSON.stringify(value);
      } catch (_error) {
        return String(value);
      }
    }

    function setupFrontendDebugLogging() {
      if (state.frontendDebugBound) {
        return;
      }
      state.frontendDebugBound = true;

      window.addEventListener("error", function (event) {
        var errorObj = event && event.error;
        var stack = errorObj && errorObj.stack ? String(errorObj.stack) : "";
        postFrontendDebug({
          source: "window.error",
          level: "ERROR",
          message: String((event && event.message) || "frontend_error"),
          page: String((window.location && window.location.pathname) || ""),
          stack: stack,
          extra: [
            "filename=" + String((event && event.filename) || ""),
            "line=" + String((event && event.lineno) || ""),
            "column=" + String((event && event.colno) || ""),
          ].join(" "),
        });
      });

      window.addEventListener("unhandledrejection", function (event) {
        var reason = event ? event.reason : "";
        var message = "";
        var stack = "";
        if (reason && typeof reason === "object") {
          var reasonText = typeof reason.toString === "function" ? reason.toString() : "";
          message = String(reason.message || reasonText || "");
          stack = String(reason.stack || "");
        } else {
          message = String(reason || "unhandled_rejection");
        }
        postFrontendDebug({
          source: "window.unhandledrejection",
          level: "ERROR",
          message: message || "unhandled_rejection",
          page: String((window.location && window.location.pathname) || ""),
          stack: stack,
        });
      });

      if (!state.frontendConsolePatched && window.console) {
        state.frontendConsolePatched = true;
        var levelMap = {
          error: "ERROR",
          warn: "WARNING",
          info: "INFO",
          debug: "DEBUG",
          log: "DEBUG",
        };
        ["error", "warn", "info", "debug", "log"].forEach(function (method) {
          if (typeof window.console[method] !== "function") return;
          var original = window.console[method];
          window.console[method] = function () {
            var args = Array.prototype.slice.call(arguments || []);
            try {
              postFrontendDebug({
                source: "console." + method,
                level: levelMap[method] || "INFO",
                message: args.map(safeFrontendText).join(" ").slice(0, 1600),
                page: String((window.location && window.location.pathname) || ""),
              });
            } catch (_error) {}
            return original.apply(window.console, args);
          };
        });
      }
    }

    function activateTab(name, saveState) {
      var target = String(name || "overview").trim().toLowerCase();
      var found = false;
      els.tabButtons.forEach(function (btn) {
        if (String(btn.getAttribute("data-lan-tab-target") || "") === target) found = true;
      });
      if (!found) target = els.tabButtons.length ? String(els.tabButtons[0].getAttribute("data-lan-tab-target") || "overview") : "overview";
      els.tabButtons.forEach(function (btn) {
        var active = String(btn.getAttribute("data-lan-tab-target") || "") === target;
        btn.classList.toggle("is-active", active);
        btn.setAttribute("aria-selected", active ? "true" : "false");
        btn.setAttribute("tabindex", active ? "0" : "-1");
      });
      els.tabPanels.forEach(function (panel) {
        var panelName = String(panel.getAttribute("data-lan-tab-panel") || "");
        var active = panelName === target;
        panel.classList.toggle("is-active", active);
        panel.hidden = !active;
      });
      if (saveState) {
        try {
          window.sessionStorage.setItem(state.storageKey, target);
        } catch (_error) {}
      }
    }

    function setDisabledOpenState(disabled, url) {
      if (!els.openSelected) return;
      if (disabled) {
        els.openSelected.setAttribute("aria-disabled", "true");
        els.openSelected.classList.add("is-disabled");
        els.openSelected.href = "#";
      } else {
        els.openSelected.setAttribute("aria-disabled", "false");
        els.openSelected.classList.remove("is-disabled");
        els.openSelected.href = url || "#";
      }
    }

    function canProxyPeer(peer) {
      if (!peer) return false;
      return !!(String(peer.panel_url || "").trim() && String(peer.panel_api_token || "").trim() && asBool(peer.remote_control_target_enabled, true));
    }

    function getPeerControlState(nodeId) {
      var node = String(nodeId || "").trim();
      if (!node) return normalizePeerControlState(null);
      return normalizePeerControlState(state.peerControlByNodeId[node]);
    }

    function hasApprovedGrantForNode(nodeId) {
      return getPeerControlState(nodeId).status === "approved";
    }

    function remoteRoleText(peer) {
      return (
        "контроллер=" +
        (asBool(peer.remote_control_controller_enabled, true) ? "вкл" : "выкл") +
        ", цель=" +
        (asBool(peer.remote_control_target_enabled, true) ? "вкл" : "выкл") +
        ", подтверждение=" +
        (asBool(peer.remote_control_require_approval, true) ? "обязательно" : "авто")
      );
    }

    function roleGroup(peer) {
      var controller = asBool(peer.remote_control_controller_enabled, true);
      var target = asBool(peer.remote_control_target_enabled, true);
      if (controller && target) return "dual";
      if (controller) return "controller_only";
      if (target) return "target_only";
      return "none";
    }

    function peerSearchText(peer) {
      return [peer.node_id || "", peer.instance_name || "", peer.app || "", peer.ip || "", peer.hostname || "", peer.role || ""].join(" ").toLowerCase();
    }

    function peerLastSeenValue(peer) {
      var num = Number(peer.last_seen);
      return isFinite(num) && num > 0 ? num : 0;
    }

    function updateSelectedState() {
      if (!els.selectedLine) return;
      var peer = state.selectedNodeId ? state.peersByNodeId[state.selectedNodeId] : null;
      if (!peer) {
        els.selectedLine.textContent = "Выбранный узел: нет.";
        setDisabledOpenState(true, "");
      } else {
        var panelUrl = String(peer.panel_url || "").trim();
        els.selectedLine.textContent =
          "Выбранный узел: " +
          String(peer.instance_name || peer.hostname || peer.ip || "узел") +
          " (" +
          String(peer.ip || "") +
          "), role=" +
          String(peer.role || "") +
          ", панель=" +
          (panelUrl || "-") +
          ", " +
          (canProxyPeer(peer) ? "доступно централизованное проксирование" : "проксирование недоступно") +
          ".";
        setDisabledOpenState(!panelUrl, panelUrl);
      }
      if (els.selectedGrant) {
        var text = grantStatusLabel(state.grantState.status || "idle");
        var msg = String(state.grantState.message || "");
        els.selectedGrant.textContent = msg ? "Разрешение: " + text + " | " + msg : "Разрешение: " + text;
      }
    }

    function applySelectedRowCss() {
      if (!els.tableBody) return;
      toArray(els.tableBody.querySelectorAll("tr[data-node-id]")).forEach(function (row) {
        var selected = row.getAttribute("data-node-id") === state.selectedNodeId;
        row.classList.toggle("is-selected", selected);
        row.setAttribute("aria-selected", selected ? "true" : "false");
      });
    }

    function selectNode(nodeId) {
      state.selectedNodeId = String(nodeId || "");
      applySelectedRowCss();
      updateSelectedState();
    }

    function createTextCell(text) {
      var td = document.createElement("td");
      td.textContent = text == null ? "" : String(text);
      return td;
    }

    function buildManageCell(peer, nodeId) {
      var td = document.createElement("td");
      var wrap = document.createElement("div");
      wrap.className = "lan-manage-actions";
      var hasPanel = String(peer.panel_url || "").trim().length > 0;
      var hasToken = String(peer.panel_api_token || "").trim().length > 0;
      var canTarget = asBool(peer.remote_control_target_enabled, true);
      var canControl = asBool(peer.remote_control_controller_enabled, true);
      var controlState = getPeerControlState(nodeId);
      var hasGrant = controlState.status === "approved";
      var isPending = controlState.status === "pending";
      var count = 0;

      if (state.canAction && hasPanel && hasToken) {
        var form = document.createElement("form");
        form.method = "post";
        form.action = state.selectUrlTemplate.replace("__NODE__", encodeURIComponent(nodeId));
        form.className = "inline-form";
        var csrf = document.createElement("input");
        csrf.type = "hidden";
        csrf.name = "csrf_token";
        csrf.value = state.csrfToken;
        form.appendChild(csrf);
        var next = document.createElement("input");
        next.type = "hidden";
        next.name = "next";
        next.value = "/";
        form.appendChild(next);
        var manageBtn = document.createElement("button");
        manageBtn.type = "submit";
        manageBtn.className = "btn";
        manageBtn.disabled = !hasGrant || nodeId === state.selectedNodeId;
        manageBtn.textContent = nodeId === state.selectedNodeId ? "Текущий" : "Управлять";
        form.appendChild(manageBtn);
        wrap.appendChild(form);
        count += 1;
        if (canTarget) {
          var requestBtn = document.createElement("button");
          requestBtn.type = "button";
          requestBtn.className = "btn";
          requestBtn.setAttribute("data-request-node-id", nodeId);
          requestBtn.disabled = hasGrant || isPending;
          requestBtn.textContent = "Запросить доступ";
          wrap.appendChild(requestBtn);
          count += 1;
        }
      }

      if (hasPanel) {
        var openLink = document.createElement("a");
        openLink.className = "btn";
        openLink.href = String(peer.panel_url || "");
        openLink.target = "_blank";
        openLink.rel = "noopener";
        openLink.textContent = "Панель";
        wrap.appendChild(openLink);
        count += 1;
      }

      if (state.canAction && canControl) {
        var policyBtn = document.createElement("button");
        policyBtn.type = "button";
        policyBtn.setAttribute("data-manager-policy-node-id", nodeId);
        if (String(state.policiesByNodeId[nodeId] || "") === "deny") {
          policyBtn.className = "btn";
          policyBtn.textContent = "Снять запрет доступа";
          policyBtn.setAttribute("data-manager-policy-decision", "prompt");
        } else {
          policyBtn.className = "btn danger";
          policyBtn.textContent = "Запретить доступ";
          policyBtn.setAttribute("data-manager-policy-decision", "deny");
        }
        wrap.appendChild(policyBtn);
        count += 1;
      }

      if (state.canAction) {
        var removeBtn = document.createElement("button");
        removeBtn.type = "button";
        removeBtn.className = "btn danger";
        removeBtn.setAttribute("data-peer-remove-node-id", nodeId);
        removeBtn.textContent = "Удалить";
        wrap.appendChild(removeBtn);
        count += 1;
      }

      if (!count) {
        var muted = document.createElement("span");
        muted.className = "muted";
        muted.textContent = state.canAction ? "-" : "требуется can_action";
        wrap.appendChild(muted);
      }
      td.appendChild(wrap);
      return td;
    }

    function buildPeerStatusCell(controlState) {
      var td = document.createElement("td");
      var stateValue = normalizePeerControlState(controlState);
      var status = String(stateValue.status || "idle");
      var chip = document.createElement("span");
      var level = "warn";
      if (status === "approved") level = "ok";
      if (["denied", "expired", "cancelled", "error"].indexOf(status) >= 0) level = "bad";
      chip.className = "status-chip " + level;
      chip.textContent = peerControlStatusLabel(status);
      td.appendChild(chip);
      if (stateValue.message) {
        var note = document.createElement("div");
        note.className = "muted";
        note.textContent = stateValue.message;
        td.appendChild(note);
      }
      return td;
    }

    function buildPeerRow(peer) {
      var tr = document.createElement("tr");
      var nodeId = String(peer.node_id || "");
      var controlState = getPeerControlState(nodeId);
      tr.setAttribute("tabindex", "0");
      tr.setAttribute("data-node-id", nodeId);
      tr.setAttribute("data-panel-url", String(peer.panel_url || ""));
      tr.setAttribute("data-panel-api-token", String(peer.panel_api_token || ""));
      tr.setAttribute("data-remote-target-enabled", asBool(peer.remote_control_target_enabled, true) ? "1" : "0");
      tr.setAttribute("aria-selected", nodeId === state.selectedNodeId ? "true" : "false");
      tr.classList.toggle("is-selected", nodeId === state.selectedNodeId);

      tr.appendChild(buildManageCell(peer, nodeId));
      tr.appendChild(createTextCell((peer.instance_name || "") + (peer.app ? " (" + peer.app + ")" : "")));
      tr.appendChild(buildPeerStatusCell(controlState));
      tr.appendChild(createTextCell((peer.ip || "") + (peer.hostname ? " / " + peer.hostname : "")));
      tr.appendChild(createTextCell(peer.role || ""));
      tr.appendChild(createTextCell(remoteRoleText(peer)));
      var panelCell = document.createElement("td");
      if (peer.panel_url) {
        var link = document.createElement("a");
        link.href = String(peer.panel_url || "");
        link.target = "_blank";
        link.rel = "noopener";
        link.textContent = String(peer.panel_url || "");
        panelCell.appendChild(link);
      } else {
        panelCell.textContent = "-";
      }
      tr.appendChild(panelCell);
      tr.appendChild(createTextCell(peer.last_seen_text || ""));
      return tr;
    }

    function buildPeersSignature(peers) {
      if (!Array.isArray(peers)) return "";
      return peers
        .map(function (peer) {
          return [
            peer.node_id || "",
            peer.instance_name || "",
            peer.app || "",
            peer.ip || "",
            peer.role || "",
            peer.hostname || "",
            peer.last_seen_text || "",
            peer.panel_url || "",
            peer.panel_api_token || "",
            asBool(peer.remote_control_target_enabled, true) ? "1" : "0",
            asBool(peer.remote_control_controller_enabled, true) ? "1" : "0",
            asBool(peer.remote_control_require_approval, true) ? "1" : "0",
          ].join("|");
        })
        .join("||");
    }

    function updateManagerMeta(total, shown) {
      if (els.managerCount) els.managerCount.textContent = String(shown);
      if (els.managerMeta) {
        els.managerMeta.textContent = "Показано: " + String(shown) + " из " + String(total) + ".";
      }
    }

    function filterPeer(peer) {
      var filter = String((els.managerFilter && els.managerFilter.value) || "all");
      var group = roleGroup(peer);
      var decision = String(state.policiesByNodeId[String(peer.node_id || "")] || "");
      var panelReady = !!(String(peer.panel_url || "").trim() && String(peer.panel_api_token || "").trim());
      if (filter === "controller" && !(group === "controller_only" || group === "dual")) return false;
      if (filter === "target" && !(group === "target_only" || group === "dual")) return false;
      if (filter === "controller_only" && group !== "controller_only") return false;
      if (filter === "target_only" && group !== "target_only") return false;
      if (filter === "dual" && group !== "dual") return false;
      if (filter === "panel_ready" && !panelReady) return false;
      if (filter === "blocked" && decision !== "deny") return false;
      var q = String((els.managerSearch && els.managerSearch.value) || "").trim().toLowerCase();
      if (q && peerSearchText(peer).indexOf(q) === -1) return false;
      return true;
    }

    function sortPeers(list) {
      var sortBy = String((els.managerSort && els.managerSort.value) || "name");
      var dir = String((els.managerOrder && els.managerOrder.value) || "asc") === "desc" ? -1 : 1;
      return list.slice().sort(function (a, b) {
        if (sortBy === "seen") return (peerLastSeenValue(a) - peerLastSeenValue(b)) * dir;
        var av = "";
        var bv = "";
        if (sortBy === "ip") {
          av = String(a.ip || "");
          bv = String(b.ip || "");
        } else if (sortBy === "role") {
          av = remoteRoleText(a);
          bv = remoteRoleText(b);
        } else if (sortBy === "panel") {
          av = String(a.panel_url || "");
          bv = String(b.panel_url || "");
        } else {
          av = String(a.instance_name || a.hostname || "");
          bv = String(b.instance_name || b.hostname || "");
        }
        return av.localeCompare(bv, "ru", { sensitivity: "base" }) * dir;
      });
    }

    function applyManagerView() {
      if (!els.tableBody) return;
      var filtered = state.peersList.filter(filterPeer);
      var sorted = sortPeers(filtered);
      var fragment = document.createDocumentFragment();
      if (!sorted.length) {
        var emptyRow = document.createElement("tr");
        var emptyCell = document.createElement("td");
        emptyCell.colSpan = 8;
        emptyCell.textContent = "Под выбранные фильтры узлы не найдены.";
        emptyRow.appendChild(emptyCell);
        fragment.appendChild(emptyRow);
      } else {
        sorted.forEach(function (peer) {
          fragment.appendChild(buildPeerRow(peer));
        });
      }
      els.tableBody.innerHTML = "";
      els.tableBody.appendChild(fragment);
      updateManagerMeta(state.peersList.length, sorted.length);
      applySelectedRowCss();
      updateSelectedState();
    }

    function renderPeers(peers) {
      state.peersList = Array.isArray(peers) ? peers.slice() : [];
      state.peersByNodeId = Object.create(null);
      state.peersList.forEach(function (peer) {
        var nodeId = String(peer.node_id || "");
        if (nodeId) state.peersByNodeId[nodeId] = peer;
      });
      if (state.selectedNodeId && !state.peersByNodeId[state.selectedNodeId]) {
        state.selectedNodeId = "";
      }
      applyManagerView();
    }

    function setFieldValue(name, value) {
      if (!els.form || !name) return;
      var field = els.form.elements[name];
      if (!field) return;
      if (field.length && !field.tagName) field = field[0];
      if (document.activeElement === field) return;
      var next = value == null ? "" : String(value);
      if (field.value !== next) field.value = next;
    }

    function setCheckboxValue(name, checked) {
      if (!els.form || !name) return;
      var input = els.form.querySelector('input[type="checkbox"][name="' + name + '"]');
      if (!input || document.activeElement === input) return;
      input.checked = !!checked;
    }

    function normalizeRoleMode(mode, fallback) {
      var value = String(mode || "").trim().toLowerCase();
      if (value === "controller" || value === "target" || value === "dual") return value;
      var fb = String(fallback || "").trim().toLowerCase();
      if (fb === "controller" || fb === "target" || fb === "dual") return fb;
      return "controller";
    }

    function roleModeFromFlags(controllerEnabled, targetEnabled) {
      if (controllerEnabled && targetEnabled) return "dual";
      if (controllerEnabled) return "controller";
      if (targetEnabled) return "target";
      return "controller";
    }

    function setRoleMode(mode) {
      if (!els.remoteRoleModeInputs.length) return;
      var normalized = normalizeRoleMode(mode, "controller");
      var matched = false;
      els.remoteRoleModeInputs.forEach(function (input) {
        var active = String(input.value || "").trim().toLowerCase() === normalized;
        input.checked = active;
        if (active) matched = true;
      });
      if (!matched && els.remoteRoleModeInputs.length) {
        els.remoteRoleModeInputs[0].checked = true;
      }
    }

    function getRoleMode() {
      if (!els.remoteRoleModeInputs.length) return "controller";
      var checked = els.remoteRoleModeInputs.filter(function (input) {
        return !!input.checked;
      });
      if (!checked.length) {
        setRoleMode("controller");
        return "controller";
      }
      var mode = normalizeRoleMode(String(checked[0].value || ""), "controller");
      if (checked.length > 1) setRoleMode(mode);
      return mode;
    }

    function toggleRemoteRoleVisibility() {
      if (!els.form) return;
      var roleMode = getRoleMode();
      var controllerEnabled = roleMode === "controller" || roleMode === "dual";
      var targetEnabled = roleMode === "target" || roleMode === "dual";
      toArray(els.form.querySelectorAll("[data-remote-controller-only]")).forEach(function (el) {
        el.classList.toggle("is-hidden", !controllerEnabled);
      });
      toArray(els.form.querySelectorAll("[data-remote-target-only]")).forEach(function (el) {
        el.classList.toggle("is-hidden", !targetEnabled);
      });
    }

    function applySettings(settings) {
      if (!settings || !els.form) return;
      setFieldValue("mode", settings.mode || "multi");
      setCheckboxValue("enabled_on_start", asBool(settings.enabled_on_start));
      setFieldValue("instance_name", settings.instance_name || "");
      setFieldValue("service_name", settings.service_name || "AutoCraft-Bot");
      setFieldValue("app_version", settings.app_version || "");
      setFieldValue("udp_port", settings.udp_port || 37555);
      setFieldValue("multicast_group", settings.multicast_group || "239.255.67.67");
      setCheckboxValue("multicast_enabled", asBool(settings.multicast_enabled));
      setCheckboxValue("broadcast_enabled", asBool(settings.broadcast_enabled));
      setFieldValue("discover_interval_sec", settings.discover_interval_sec || 5.0);
      setFieldValue("announce_interval_sec", settings.announce_interval_sec || 10.0);
      setFieldValue("peer_timeout_sec", settings.peer_timeout_sec || 30.0);
      setCheckboxValue("advertise_panel", asBool(settings.advertise_panel));
      setFieldValue("panel_scheme", settings.panel_scheme || "http");
      setFieldValue("panel_port", settings.panel_port || 5212);
      setFieldValue("panel_path", settings.panel_path || "/");
      setFieldValue("panel_host_override", settings.panel_host_override || "");
      setRoleMode(
        settings.remote_role_mode ||
          roleModeFromFlags(
            asBool(settings.remote_control_controller_enabled, true),
            asBool(settings.remote_control_target_enabled, true)
          )
      );
      setCheckboxValue("remote_control_require_approval", asBool(settings.remote_control_require_approval, true));
      setCheckboxValue("remote_control_auto_request_on_select", asBool(settings.remote_control_auto_request_on_select, true));
      setFieldValue("remote_control_request_timeout_sec", settings.remote_control_request_timeout_sec || 180);
      setFieldValue("remote_control_grant_ttl_sec", settings.remote_control_grant_ttl_sec || 1800);
      toggleRemoteRoleVisibility();
    }

    function updateStatus(statusText, status) {
      if (els.statusLine) els.statusLine.textContent = String(statusText || "");
      var running = !!(status && status.running);
      if (els.runningChip) setChipState(els.runningChip, running ? "ok" : "warn", running ? "Служба запущена" : "Служба остановлена");
      updateServiceToggleButton(running);
    }

    function updateLog(logLines) {
      if (!els.logEl || !Array.isArray(logLines)) return;
      var sig = String(logLines.length) + ":" + (logLines.length ? String(logLines[logLines.length - 1]) : "");
      if (sig === state.logsSignature) return;
      var keepBottom = els.logEl.scrollTop + els.logEl.clientHeight >= els.logEl.scrollHeight - 4;
      var prevScroll = els.logEl.scrollTop;
      els.logEl.textContent = logLines.join("\n");
      els.logEl.scrollTop = keepBottom ? els.logEl.scrollHeight : prevScroll;
      state.logsSignature = sig;
    }

    function renderPendingRequests(items) {
      if (!els.requestsBody) return;
      var list = Array.isArray(items) ? items : [];
      if (els.requestCount) els.requestCount.textContent = String(list.length);
      els.requestsBody.innerHTML = "";
      if (!list.length) {
        var emptyRow = document.createElement("tr");
        var emptyCell = document.createElement("td");
        emptyCell.colSpan = 6;
        emptyCell.textContent = "Нет ожидающих запросов.";
        emptyRow.appendChild(emptyCell);
        els.requestsBody.appendChild(emptyRow);
        return;
      }
      list.forEach(function (req) {
        var row = document.createElement("tr");
        row.setAttribute("data-request-id", String(req.request_id || ""));
        row.appendChild(createTextCell(req.controller_name || req.controller_node_id || ""));
        row.appendChild(createTextCell(req.controller_ip || ""));
        row.appendChild(createTextCell(req.requester_user || ""));
        row.appendChild(createTextCell(req.requested_roles || ""));
        row.appendChild(createTextCell(req.created_at || ""));
        var cell = document.createElement("td");
        if (!state.canAction) {
          cell.textContent = "требуется can_action";
        } else {
          var wrap = document.createElement("div");
          wrap.className = "request-action-wrap";
          wrap.innerHTML = '<label class="autocraft-checkbox compact"><input type="checkbox" data-request-remember checked><span>Запомнить</span></label><input type="text" data-request-note placeholder="Комментарий"><button class="btn-primary" type="button" data-request-approve="1">Разрешить</button><button class="btn danger" type="button" data-request-approve="0">Отклонить</button>';
          cell.appendChild(wrap);
        }
        row.appendChild(cell);
        els.requestsBody.appendChild(row);
      });
    }

    function updateQuickRequestAlert(items) {
      if (!els.quickRequestAlert) return;
      var list = Array.isArray(items) ? items : [];
      if (!state.canAction || !list.length) {
        els.quickRequestAlert.hidden = true;
        els.quickRequestAlert.setAttribute("data-request-id", "");
        return;
      }

      var req = list[0] || {};
      var requestId = String(req.request_id || "").trim();
      if (!requestId) {
        els.quickRequestAlert.hidden = true;
        els.quickRequestAlert.setAttribute("data-request-id", "");
        return;
      }

      els.quickRequestAlert.hidden = false;
      els.quickRequestAlert.setAttribute("data-request-id", requestId);
      if (els.quickRequestCount) els.quickRequestCount.textContent = String(list.length);
      if (els.quickRequestMessage) {
        els.quickRequestMessage.textContent =
          "Поступил запрос от узла " +
          String(req.controller_name || req.controller_node_id || "-") +
          ". Подтвердите или отклоните доступ.";
      }
      if (els.quickRequestController) {
        els.quickRequestController.textContent = String(req.controller_name || req.controller_node_id || "-");
      }
      if (els.quickRequestIp) els.quickRequestIp.textContent = String(req.controller_ip || "-");
      if (els.quickRequestUser) els.quickRequestUser.textContent = String(req.requester_user || "-");
      if (els.quickRequestRoles) els.quickRequestRoles.textContent = String(req.requested_roles || "-");
      if (els.quickRequestCreated) els.quickRequestCreated.textContent = String(req.created_at || "-");

      if (requestId !== state.lastQuickRequestId) {
        state.lastQuickRequestId = requestId;
        announceLive(
          "Новый запрос удаленного доступа от узла " + String(req.controller_name || req.controller_node_id || "-") + ".",
          true
        );
      }
    }

    function renderPolicies(items) {
      var list = Array.isArray(items) ? items : [];
      state.policiesByNodeId = Object.create(null);
      list.forEach(function (p) {
        var nodeId = String(p.controller_node_id || "").trim();
        if (nodeId) state.policiesByNodeId[nodeId] = String(p.decision || "").trim().toLowerCase();
      });
      if (!els.policiesBody) {
        applyManagerView();
        return;
      }
      els.policiesBody.innerHTML = "";
      if (!list.length) {
        var emptyRow = document.createElement("tr");
        var emptyCell = document.createElement("td");
        emptyCell.colSpan = 6;
        emptyCell.textContent = "Нет сохраненных правил.";
        emptyRow.appendChild(emptyCell);
        els.policiesBody.appendChild(emptyRow);
        applyManagerView();
        return;
      }
      list.forEach(function (p) {
        var row = document.createElement("tr");
        row.setAttribute("data-controller-node-id", String(p.controller_node_id || ""));
        row.appendChild(createTextCell(p.controller_name || p.controller_node_id || ""));
        row.appendChild(createTextCell(p.controller_ip || ""));
        row.appendChild(createTextCell(decisionLabel(p.decision || "")));
        row.appendChild(createTextCell(p.updated_by || ""));
        row.appendChild(createTextCell(p.updated_at || ""));
        var cell = document.createElement("td");
        if (!state.canAction) {
          cell.textContent = "требуется can_action";
        } else {
          var wrap = document.createElement("div");
          wrap.className = "policy-action-wrap";
          var decision = String(p.decision || "").trim().toLowerCase();
          var allowBtn = document.createElement("button");
          allowBtn.type = "button";
          allowBtn.className = "btn-primary";
          allowBtn.setAttribute("data-policy-decision", "allow");
          allowBtn.disabled = decision === "allow";
          allowBtn.textContent = "Разрешить";
          wrap.appendChild(allowBtn);

          var denyBtn = document.createElement("button");
          denyBtn.type = "button";
          denyBtn.className = "btn danger";
          denyBtn.setAttribute("data-policy-decision", "deny");
          denyBtn.disabled = decision === "deny";
          denyBtn.textContent = "Отклонить";
          wrap.appendChild(denyBtn);

          var promptBtn = document.createElement("button");
          promptBtn.type = "button";
          promptBtn.className = "btn";
          promptBtn.setAttribute("data-policy-decision", "prompt");
          promptBtn.disabled = decision === "prompt";
          promptBtn.textContent = "Спрашивать каждый раз";
          wrap.appendChild(promptBtn);
          cell.appendChild(wrap);
        }
        row.appendChild(cell);
        els.policiesBody.appendChild(row);
      });
      applyManagerView();
    }

    function updateOverview(settings, status, peers, remoteAccess) {
      var runtime = status || {};
      var cfg = settings || {};
      var peerList = Array.isArray(peers) ? peers : [];
      var access = remoteAccess || {};
      var running = asBool(runtime.running, false);
      var peerCount = peerList.length;
      var pendingCount = parseInt(access.pending_count, 10);
      if (!isFinite(pendingCount)) pendingCount = Array.isArray(access.pending_requests) ? access.pending_requests.length : 0;
      var lastError = String(runtime.last_error || "").trim();

      setChipState(els.summaryServiceChip, running ? "ok" : "warn", running ? "работает" : "остановлена");
      if (els.summaryMode) els.summaryMode.textContent = modeLabel(cfg.mode || "multi");
      setChipState(els.summaryPeersChip, peerCount ? "ok" : "warn", String(peerCount));
      if (els.summaryPeers) els.summaryPeers.textContent = peerCount ? "узлы обнаружены" : "узлы не обнаружены";
      if (els.summaryMulticast) {
        var mcEnabled = asBool(cfg.multicast_enabled, true);
        var mcJoined = asBool(runtime.multicast_joined, false);
        els.summaryMulticast.textContent = mcEnabled ? (mcJoined ? "включён, канал активен" : "включён, нет подтверждения") : "выключен";
        setStateClass(els.summaryMulticast, mcEnabled && mcJoined ? "ok" : "warn");
      }
      if (els.summaryPanel) {
        var panelEnabled = asBool(cfg.advertise_panel, true);
        els.summaryPanel.textContent = panelEnabled ? "включена" : "выключена";
        setStateClass(els.summaryPanel, panelEnabled ? "ok" : "warn");
      }
      if (els.summaryRemote) {
        els.summaryRemote.textContent =
          "контроллер=" +
          (asBool(cfg.remote_control_controller_enabled, true) ? "вкл" : "выкл") +
          ", цель=" +
          (asBool(cfg.remote_control_target_enabled, true) ? "вкл" : "выкл") +
          ", подтверждение=" +
          (asBool(cfg.remote_control_require_approval, true) ? "обязательно" : "авто");
      }
      if (els.summaryRequests) els.summaryRequests.textContent = String(Math.max(0, pendingCount));
      if (els.summaryError) {
        if (lastError) {
          els.summaryError.textContent = lastError;
          setStateClass(els.summaryError, "bad");
          setChipState(els.summaryErrorChip, "bad", "есть ошибки");
        } else {
          els.summaryError.textContent = "Системных ошибок не обнаружено.";
          setStateClass(els.summaryError, "ok");
          setChipState(els.summaryErrorChip, "ok", "ошибок нет");
        }
      }
      var snapshot = "service:" + (running ? "up" : "down") + "|peers:" + String(peerCount) + "|pending:" + String(pendingCount) + "|error:" + (lastError ? "1" : "0");
      if (snapshot !== state.lastSummarySnapshot) {
        state.lastSummarySnapshot = snapshot;
        announceLive(
          "Статус ЛВС: " +
            (running ? "служба работает" : "служба остановлена") +
            ", узлов: " +
            String(peerCount) +
            ", входящих запросов: " +
            String(Math.max(0, pendingCount)) +
            (lastError ? ", есть ошибка." : ", ошибок нет."),
          false
        );
      }
    }

    function applyState(data) {
      if (!data || data.ok === false) return;
      if (Object.prototype.hasOwnProperty.call(data, "selected_node_id")) {
        state.selectedNodeId = String(data.selected_node_id || "");
      }
      if (typeof data.can_action !== "undefined" && data.can_action !== null) state.canAction = asBool(data.can_action, state.canAction);
      var remoteAccess = data.remote_access || {};
      var nextPeerControl = Object.create(null);
      var peerControlRaw = remoteAccess.peer_control_states || {};
      if (peerControlRaw && typeof peerControlRaw === "object") {
        Object.keys(peerControlRaw).forEach(function (nodeId) {
          var key = String(nodeId || "").trim();
          if (!key) return;
          nextPeerControl[key] = normalizePeerControlState(peerControlRaw[key]);
        });
      }
      state.peerControlByNodeId = nextPeerControl;
      state.grantState = remoteAccess.grant_state || state.grantState || { status: "idle", message: "" };
      if (state.selectedNodeId) {
        var selectedNode = String(state.selectedNodeId || "").trim();
        if (selectedNode) {
          var selectedState = normalizePeerControlState(
            Object.assign({}, state.peerControlByNodeId[selectedNode] || {}, state.grantState || {})
          );
          state.peerControlByNodeId[selectedNode] = selectedState;
        }
      }

      updateStatus(data.status_text, data.status || {});
      updateDebugLogging(data.debug_logging || {});

      var nextSettingsSignature = String(data.settings_signature || "");
      if (!nextSettingsSignature) {
        try {
          nextSettingsSignature = JSON.stringify(data.settings || {});
        } catch (_error) {
          nextSettingsSignature = "";
        }
      }
      if (nextSettingsSignature !== state.settingsSignature) {
        applySettings(data.settings || {});
        state.settingsSignature = nextSettingsSignature;
      } else {
        toggleRemoteRoleVisibility();
      }

      var nextPeersSignature = String(data.peers_signature || "");
      if (!nextPeersSignature) nextPeersSignature = buildPeersSignature(data.peers || []);
      if (nextPeersSignature !== state.peersSignature) {
        renderPeers(data.peers || []);
        state.peersSignature = nextPeersSignature;
      } else {
        applyManagerView();
      }

      renderPendingRequests(remoteAccess.pending_requests || []);
      updateQuickRequestAlert(remoteAccess.pending_requests || []);
      renderPolicies(remoteAccess.saved_policies || []);
      updateOverview(data.settings || {}, data.status || {}, data.peers || [], remoteAccess);
      updateLog(data.logs || []);
    }

    function requestState() {
      if (state.pollingInFlight) return;
      state.pollingInFlight = true;
      fetch(stateUrl, { credentials: "same-origin", cache: "no-store", headers: { Accept: "application/json" } })
        .then(function (response) {
          if (!response.ok) throw new Error("lan_state_bad_response");
          return response.json();
        })
        .then(function (data) {
          applyState(data || {});
        })
        .catch(function () {})
        .finally(function () {
          state.pollingInFlight = false;
        });
    }

    function requestAccess(nodeId, button) {
      if (!nodeId) return;
      if (button) button.disabled = true;
      postJson(state.requestUrlTemplate.replace("__NODE__", encodeURIComponent(nodeId)), {})
        .then(function (data) {
          var entry = normalizePeerControlState((data && data.peer_control_state) || data || {});
          state.peerControlByNodeId[String(nodeId)] = entry;
          if (String(state.selectedNodeId || "") === String(nodeId || "")) {
            state.grantState = Object.assign({}, state.grantState || {}, {
              status: entry.status || "idle",
              message: entry.message || "",
              request_id: entry.request_id || "",
            });
          }
          updateSelectedState();
          applyManagerView();
          setStatusText(data.message || "Запрос отправлен.");
          requestState();
        })
        .catch(function (error) {
          setStatusText(error && error.message ? error.message : "Не удалось отправить запрос.");
          requestState();
        })
        .finally(function () {
          if (button) button.disabled = false;
        });
    }

    function respondRequest(requestId, approve, row, button) {
      if (!requestId) return;
      var payload = {
        approve: approve ? 1 : 0,
        remember: row && row.querySelector("[data-request-remember]") && row.querySelector("[data-request-remember]").checked ? 1 : 0,
        note: row && row.querySelector("[data-request-note]") ? String(row.querySelector("[data-request-note]").value || "") : "",
      };
      if (button) button.disabled = true;
      postJson(state.respondUrlTemplate.replace("__REQ__", encodeURIComponent(requestId)), payload)
        .then(function (data) {
          setStatusText(data.message || "Запрос обработан.");
          requestState();
        })
        .catch(function (error) {
          setStatusText(error && error.message ? error.message : "Не удалось обработать запрос.");
        })
        .finally(function () {
          if (button) button.disabled = false;
        });
    }

    function savePolicy(nodeId, decision, row, button, peer) {
      if (!nodeId) return;
      var payload = {
        decision: decision,
        remember: decision === "prompt" ? 0 : 1,
        controller_name: peer ? String(peer.instance_name || peer.hostname || peer.node_id || "") : row && row.children[0] ? String(row.children[0].textContent || "") : "",
        controller_ip: peer ? String(peer.ip || "") : row && row.children[1] ? String(row.children[1].textContent || "") : "",
      };
      if (button) button.disabled = true;
      postJson(state.policyUrlTemplate.replace("__NODE__", encodeURIComponent(nodeId)), payload)
        .then(function () {
          setStatusText("Правило обновлено.");
          requestState();
        })
        .catch(function (error) {
          setStatusText(error && error.message ? error.message : "Не удалось обновить правило.");
        })
        .finally(function () {
          if (button) button.disabled = false;
        });
    }

    function removePeer(nodeId, button) {
      if (!nodeId) return;
      if (!window.confirm("Удалить этот компьютер из списка обнаружения?")) return;
      if (button) button.disabled = true;
      postJson(state.removePeerUrlTemplate.replace("__NODE__", encodeURIComponent(nodeId)), {})
        .then(function (data) {
          if (state.selectedNodeId === nodeId) {
            state.selectedNodeId = "";
            state.grantState = { status: "idle", message: "" };
          }
          delete state.peerControlByNodeId[String(nodeId)];
          setStatusText((data && data.message) || "Узел удален.");
          requestState();
        })
        .catch(function (error) {
          setStatusText(error && error.message ? error.message : "Не удалось удалить узел.");
        })
        .finally(function () {
          if (button) button.disabled = false;
        });
    }

    if (els.tabButtons.length && els.tabPanels.length) {
      els.tabButtons.forEach(function (btn) {
        btn.addEventListener("click", function () {
          activateTab(btn.getAttribute("data-lan-tab-target") || "overview", true);
        });
        btn.addEventListener("keydown", function (event) {
          var key = String(event.key || "");
          if (["ArrowLeft", "ArrowRight", "Home", "End"].indexOf(key) === -1) return;
          event.preventDefault();
          var total = els.tabButtons.length;
          var idx = els.tabButtons.indexOf(btn);
          if (idx < 0 || !total) return;
          var next = idx;
          if (key === "ArrowLeft") next = (idx - 1 + total) % total;
          if (key === "ArrowRight") next = (idx + 1) % total;
          if (key === "Home") next = 0;
          if (key === "End") next = total - 1;
          var targetBtn = els.tabButtons[next];
          if (!targetBtn) return;
          targetBtn.focus();
          activateTab(targetBtn.getAttribute("data-lan-tab-target") || "overview", true);
        });
      });
      var initialTab = "overview";
      try {
        initialTab = window.sessionStorage.getItem(state.storageKey) || initialTab;
      } catch (_error) {}
      activateTab(initialTab, false);
    }

    if (els.tableBody) {
      els.tableBody.addEventListener("click", function (event) {
        var requestBtn = event.target.closest("[data-request-node-id]");
        if (requestBtn) {
          event.preventDefault();
          requestAccess(requestBtn.getAttribute("data-request-node-id") || "", requestBtn);
          return;
        }
        var policyBtn = event.target.closest("[data-manager-policy-decision]");
        if (policyBtn) {
          event.preventDefault();
          var pNode = policyBtn.getAttribute("data-manager-policy-node-id") || "";
          savePolicy(pNode, policyBtn.getAttribute("data-manager-policy-decision") || "prompt", null, policyBtn, state.peersByNodeId[pNode] || null);
          return;
        }
        var removeBtn = event.target.closest("[data-peer-remove-node-id]");
        if (removeBtn) {
          event.preventDefault();
          removePeer(removeBtn.getAttribute("data-peer-remove-node-id") || "", removeBtn);
          return;
        }
        var row = event.target.closest("tr[data-node-id]");
        if (row) selectNode(row.getAttribute("data-node-id") || "");
      });

      els.tableBody.addEventListener("keydown", function (event) {
        var row = event.target.closest("tr[data-node-id]");
        if (!row) return;
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          selectNode(row.getAttribute("data-node-id") || "");
        }
      });
    }

    if (els.requestsBody) {
      els.requestsBody.addEventListener("click", function (event) {
        var btn = event.target.closest("[data-request-approve]");
        if (!btn) return;
        var row = btn.closest("tr[data-request-id]");
        if (!row) return;
        respondRequest(row.getAttribute("data-request-id") || "", btn.getAttribute("data-request-approve") === "1", row, btn);
      });
    }

    if (els.quickRequestAlert) {
      els.quickRequestAlert.addEventListener("click", function (event) {
        var btn = event.target.closest("[data-quick-request-approve]");
        if (!btn) return;
        var requestId = String(els.quickRequestAlert.getAttribute("data-request-id") || "").trim();
        if (!requestId) return;
        respondRequest(
          requestId,
          btn.getAttribute("data-quick-request-approve") === "1",
          els.quickRequestAlert,
          btn
        );
      });
    }

    if (els.quickOpenAccessBtn) {
      els.quickOpenAccessBtn.addEventListener("click", function () {
        activateTab("access", true);
      });
    }

    if (els.policiesBody) {
      els.policiesBody.addEventListener("click", function (event) {
        var btn = event.target.closest("[data-policy-decision]");
        if (!btn) return;
        var row = btn.closest("tr[data-controller-node-id]");
        if (!row) return;
        savePolicy(row.getAttribute("data-controller-node-id") || "", btn.getAttribute("data-policy-decision") || "prompt", row, btn, null);
      });
    }

    if (els.managerFilter) els.managerFilter.addEventListener("change", applyManagerView);
    if (els.managerSort) els.managerSort.addEventListener("change", applyManagerView);
    if (els.managerOrder) els.managerOrder.addEventListener("change", applyManagerView);
    if (els.managerSearch) els.managerSearch.addEventListener("input", applyManagerView);

    if (els.openSelected) {
      els.openSelected.addEventListener("click", function (event) {
        if (els.openSelected.getAttribute("aria-disabled") === "true") event.preventDefault();
      });
    }

    if (els.form) {
      els.form.addEventListener("change", function (event) {
        if (event.target && event.target.name === "remote_role_mode") {
          toggleRemoteRoleVisibility();
        }
      });
      setRoleMode(getRoleMode());
      toggleRemoteRoleVisibility();
    }

    updateDebugLogging({
      enabled: !!(els.form && els.form.querySelector('input[name="debug_enabled"]:checked')),
      level: els.debugLevelField ? String(els.debugLevelField.value || "") : "",
      max_mb: els.debugMaxMbField ? Number(els.debugMaxMbField.value || 0) : 0,
      backup_count: els.debugBackupCountField ? Number(els.debugBackupCountField.value || 0) : 0,
      path: els.debugPathValue ? String(els.debugPathValue.textContent || "") : "",
    });
    setupFrontendDebugLogging();
    updateServiceToggleButton(asBool(els.runningChip && els.runningChip.classList.contains("ok"), false));
    updateSelectedState();
    requestState();
    window.setInterval(function () {
      if (!document.hidden) requestState();
    }, 2500);
  }

  onReady(setupLanServersPage);
})();
