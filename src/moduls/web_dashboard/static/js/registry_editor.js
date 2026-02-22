(function () {
  "use strict";

  var config = window.registryConfig || {};
  var state = {
    currentPath: "",
    view: config.defaultView || "default",
    selectedValueName: null,
    selectedKeyPath: null,
    loading: false,
  };

  var exportProgressTimer = null;

  function $(id) {
    return document.getElementById(id);
  }

  function onReady(fn) {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", fn, { once: true });
    } else {
      fn();
    }
  }

  function setStatus(text, kind) {
    var el = $("reg-status");
    if (!el) {
      return;
    }
    el.textContent = text;
    el.classList.remove("reg-status-success", "reg-status-error", "reg-status-warning");
    if (kind) {
      el.classList.add("reg-status-" + kind);
    }
  }

  function showMessage(target, text, kind) {
    if (!target) {
      return;
    }
    target.textContent = text;
    target.classList.remove("reg-status-success", "reg-status-error", "reg-status-warning");
    if (kind) {
      target.classList.add("reg-status-" + kind);
    }
  }

  function fetchJson(url, options) {
    var opts = options || {};
    opts.headers = opts.headers || {};
    opts.headers["X-Requested-With"] = "XMLHttpRequest";
    if (opts.method && opts.method.toUpperCase() !== "GET") {
      opts.headers["X-CSRFToken"] = config.csrfToken || "";
    }
    return fetch(url, opts)
      .then(function (resp) {
        return resp
          .json()
          .catch(function () {
            return { ok: false, error: "Ошибка ответа." };
          })
          .then(function (data) {
            if (!resp.ok) {
              data.ok = false;
            }
            return data;
          });
      })
      .catch(function () {
        return { ok: false, error: "Не удалось связаться с сервером." };
      });
  }

  function normalizePath(value) {
    return (value || "").replace(/\//g, "\\").trim();
  }

  function buildPath(base, name) {
    if (!base) {
      return name || "";
    }
    if (!name) {
      return base;
    }
    return base.replace(/\\+$/, "") + "\\" + name.replace(/^\\+/, "");
  }

  function setPathInput(value) {
    var input = $("reg-path-input");
    if (input) {
      input.value = value || "";
    }
  }

  function updateBreadcrumbs(path) {
    var container = $("reg-breadcrumbs");
    if (!container) {
      return;
    }
    container.innerHTML = "";
    if (!path) {
      return;
    }
    var parts = path.split("\\").filter(Boolean);
    var current = "";
    parts.forEach(function (part, index) {
      current = current ? current + "\\" + part : part;
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "reg-breadcrumb";
      btn.textContent = part;
      btn.addEventListener("click", function () {
        loadPath(current);
      });
      container.appendChild(btn);
      if (index < parts.length - 1) {
        var divider = document.createElement("span");
        divider.className = "reg-breadcrumb-divider";
        divider.textContent = "/";
        container.appendChild(divider);
      }
    });
  }

  function renderRoots() {
    var tree = $("reg-tree");
    if (!tree) {
      return;
    }
    tree.innerHTML = "";
    var list = document.createElement("ul");
    (config.roots || []).forEach(function (root) {
      var li = document.createElement("li");
      var node = document.createElement("div");
      node.className = "reg-tree-node";
      var toggle = document.createElement("button");
      toggle.type = "button";
      toggle.className = "reg-tree-toggle";
      toggle.textContent = "+";
      toggle.disabled = true;
      var label = document.createElement("button");
      label.type = "button";
      label.className = "reg-tree-label";
      label.textContent = root.label || root.value;
      label.addEventListener("click", function () {
        loadPath(root.value);
      });
      node.appendChild(toggle);
      node.appendChild(label);
      li.appendChild(node);
      list.appendChild(li);
    });
    tree.appendChild(list);
  }

  function renderTreeNode(nodeData, parentList) {
    var li = document.createElement("li");
    var node = document.createElement("div");
    node.className = "reg-tree-node";
    if (nodeData.path === state.selectedKeyPath) {
      node.classList.add("selected");
    }

    var toggle = document.createElement("button");
    toggle.type = "button";
    toggle.className = "reg-tree-toggle";
    toggle.textContent = nodeData.has_children ? "+" : "·";
    toggle.disabled = !nodeData.has_children;

    var label = document.createElement("button");
    label.type = "button";
    label.className = "reg-tree-label";
    label.textContent = nodeData.name;
    label.addEventListener("click", function () {
      state.selectedKeyPath = nodeData.path;
      loadPath(nodeData.path);
    });

    toggle.addEventListener("click", function () {
      if (toggle.disabled) {
        return;
      }
      var childList = li.querySelector("ul");
      if (childList) {
        childList.remove();
        toggle.textContent = "+";
        return;
      }
      toggle.textContent = "…";
      fetchJson(config.keyUrl + "?path=" + encodeURIComponent(nodeData.path) + "&view=" + encodeURIComponent(state.view))
        .then(function (data) {
          if (!data.ok) {
            toggle.textContent = "+";
            setStatus(data.error || "Ошибка загрузки ключа.", "error");
            return;
          }
          var list = document.createElement("ul");
          (data.data.subkeys || []).forEach(function (child) {
            renderTreeNode(child, list);
          });
          li.appendChild(list);
          toggle.textContent = "−";
        });
    });

    node.appendChild(toggle);
    node.appendChild(label);
    li.appendChild(node);
    parentList.appendChild(li);
  }

  function renderTree(data) {
    var tree = $("reg-tree");
    if (!tree) {
      return;
    }
    tree.innerHTML = "";
    var list = document.createElement("ul");
    (data.subkeys || []).forEach(function (child) {
      renderTreeNode(child, list);
    });
    tree.appendChild(list);
  }

  function renderValues(values) {
    var tbody = $("reg-values-body");
    if (!tbody) {
      return;
    }
    tbody.innerHTML = "";
    if (!values || !values.length) {
      var row = document.createElement("tr");
      var cell = document.createElement("td");
      cell.colSpan = 3;
      cell.textContent = "Значения не найдены.";
      row.appendChild(cell);
      tbody.appendChild(row);
      return;
    }
    values.forEach(function (value) {
      var row = document.createElement("tr");
      row.className = "reg-value-row";
      row.dataset.name = value.name;
      if (value.name === (state.selectedValueName || "")) {
        row.classList.add("selected");
      }
      var nameCell = document.createElement("td");
      nameCell.textContent = value.display_name;
      var typeCell = document.createElement("td");
      typeCell.textContent = value.type;
      var dataCell = document.createElement("td");
      dataCell.textContent = value.data_preview || "";
      row.appendChild(nameCell);
      row.appendChild(typeCell);
      row.appendChild(dataCell);
      row.addEventListener("click", function () {
        state.selectedValueName = value.name;
        highlightSelectedValue();
      });
      row.addEventListener("dblclick", function () {
        if (config.canWrite) {
          openValueModal(value.name);
        }
      });
      tbody.appendChild(row);
    });
  }

  function highlightSelectedValue() {
    var rows = document.querySelectorAll(".reg-value-row");
    rows.forEach(function (row) {
      var name = row.dataset.name || "";
      if (name === (state.selectedValueName || "")) {
        row.classList.add("selected");
      } else {
        row.classList.remove("selected");
      }
    });
  }

  function loadPath(path) {
    if (!config.available) {
      setStatus(config.message || "Реестр недоступен.", "error");
      return;
    }
    var normalized = normalizePath(path);
    if (!normalized) {
      renderRoots();
      setPathInput("");
      updateBreadcrumbs("");
      renderValues([]);
      state.currentPath = "";
      state.selectedKeyPath = null;
      return;
    }
    state.loading = true;
    setStatus("Загрузка...", "warning");
    fetchJson(config.keyUrl + "?path=" + encodeURIComponent(normalized) + "&view=" + encodeURIComponent(state.view))
      .then(function (data) {
        state.loading = false;
        if (!data.ok) {
          setStatus(data.error || "Ошибка чтения реестра.", "error");
          return;
        }
        state.currentPath = data.data.path;
        state.selectedKeyPath = data.data.path;
        setPathInput(data.data.path);
        updateBreadcrumbs(data.data.path);
        renderTree(data.data);
        renderValues(data.data.values || []);
        state.selectedValueName = null;
        showMessage($("reg-value-hint"), "Выберите значение для просмотра или редактирования.", "");
        setStatus("Готово", "success");
      });
  }

  function openModal(modal) {
    if (!modal) {
      return;
    }
    modal.hidden = false;
    document.body.classList.add("modal-open");
    var input = modal.querySelector("input, textarea, select");
    if (input) {
      input.focus();
    }
  }

  function closeModal(modal) {
    if (!modal) {
      return;
    }
    modal.hidden = true;
    document.body.classList.remove("modal-open");
  }

  function setupModalClose() {
    document.querySelectorAll("[data-modal-close]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        closeModal(btn.closest(".reg-modal"));
      });
    });
  }

  function openKeyModal() {
    var modal = $("reg-modal-key");
    if (!modal) {
      return;
    }
    $("reg-key-name").value = "";
    openModal(modal);
  }

  function openRenameModal() {
    var modal = $("reg-modal-rename");
    if (!modal) {
      return;
    }
    $("reg-rename-name").value = "";
    openModal(modal);
  }

  function buildValueTypeOptions(selected) {
    var select = $("reg-value-type");
    if (!select) {
      return;
    }
    select.innerHTML = "";
    (config.types || []).forEach(function (item) {
      var opt = document.createElement("option");
      opt.value = item.value;
      opt.textContent = item.label;
      if (item.value === selected) {
        opt.selected = true;
      }
      select.appendChild(opt);
    });
  }

  function valueHelpText(valueType) {
    switch (valueType) {
      case "REG_DWORD":
        return "Введите число (10) или 0x... для шестнадцатеричного формата.";
      case "REG_QWORD":
        return "Введите число (10) или 0x... для шестнадцатеричного формата.";
      case "REG_MULTI_SZ":
        return "Каждая строка будет отдельным элементом.";
      case "REG_BINARY":
      case "REG_NONE":
        return "Введите шестнадцатеричные байты, например: 0A FF 1B.";
      default:
        return "";
    }
  }

  function openValueModal(name) {
    var modal = $("reg-modal-value");
    if (!modal) {
      return;
    }
    var isEdit = typeof name !== "undefined" && name !== null;
    var title = $("reg-modal-value-title");
    if (title) {
      title.textContent = isEdit ? "Изменить значение" : "Новое значение";
    }
    var nameInput = $("reg-value-name");
    var dataInput = $("reg-value-data");
    var help = $("reg-value-help");

    if (!isEdit) {
      nameInput.value = "";
      buildValueTypeOptions("REG_SZ");
      dataInput.value = "";
      if (help) {
        help.textContent = valueHelpText("REG_SZ");
      }
      openModal(modal);
      return;
    }

    fetchJson(config.valueUrl + "?path=" + encodeURIComponent(state.currentPath) + "&name=" + encodeURIComponent(name || "") + "&view=" + encodeURIComponent(state.view))
      .then(function (data) {
        if (!data.ok) {
          setStatus(data.error || "Ошибка загрузки значения.", "error");
          return;
        }
        var value = data.data || {};
        nameInput.value = value.name || "";
        buildValueTypeOptions(value.type || "REG_SZ");
        dataInput.value = value.data_raw || value.data_preview || "";
        if (help) {
          help.textContent = valueHelpText(value.type);
        }
        openModal(modal);
      });
  }

  function confirmAction(message) {
    if (window.panelConfirm) {
      return window.panelConfirm(message);
    }
    return Promise.resolve(window.confirm(message));
  }

  function createKey() {
    var name = ($("reg-key-name").value || "").trim();
    if (!name) {
      setStatus("Введите имя ключа.", "error");
      return;
    }
    var path = buildPath(state.currentPath, name);
    fetchJson(config.keyCreateUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: path, view: state.view }),
    }).then(function (data) {
      if (!data.ok) {
        setStatus(data.error || "Не удалось создать ключ.", "error");
        return;
      }
      closeModal($("reg-modal-key"));
      setStatus(data.message || "Ключ создан.", "success");
      loadPath(state.currentPath);
    });
  }

  function renameKey() {
    var newName = ($("reg-rename-name").value || "").trim();
    if (!newName) {
      setStatus("Введите новое имя ключа.", "error");
      return;
    }
    var target = state.selectedKeyPath || state.currentPath;
    fetchJson(config.keyRenameUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: target, new_name: newName, view: state.view }),
    }).then(function (data) {
      if (!data.ok) {
        setStatus(data.error || "Не удалось переименовать ключ.", "error");
        return;
      }
      closeModal($("reg-modal-rename"));
      setStatus(data.message || "Ключ переименован.", "success");
      loadPath(state.currentPath);
    });
  }

  function deleteKey() {
    var target = state.selectedKeyPath || state.currentPath;
    if (!target) {
      setStatus("Выберите ключ.", "error");
      return;
    }
    confirmAction("Удалить выбранный ключ и все вложенные?" + "\n" + target).then(function (ok) {
      if (!ok) {
        return;
      }
      fetchJson(config.keyDeleteUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: target, view: state.view, recursive: true }),
      }).then(function (data) {
        if (!data.ok) {
          setStatus(data.error || "Не удалось удалить ключ.", "error");
          return;
        }
        setStatus(data.message || "Ключ удален.", "success");
        var parent = target.split("\\");
        parent.pop();
        loadPath(parent.join("\\") || "");
      });
    });
  }

  function saveValue() {
    var name = $("reg-value-name").value || "";
    var valueType = $("reg-value-type").value;
    var data = $("reg-value-data").value;
    fetchJson(config.valueSetUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        path: state.currentPath,
        name: name,
        value_type: valueType,
        data: data,
        view: state.view,
      }),
    }).then(function (dataResp) {
      if (!dataResp.ok) {
        setStatus(dataResp.error || "Не удалось сохранить значение.", "error");
        return;
      }
      closeModal($("reg-modal-value"));
      setStatus(dataResp.message || "Значение сохранено.", "success");
      loadPath(state.currentPath);
    });
  }

  function deleteValue() {
    if (state.selectedValueName === null) {
      setStatus("Выберите значение для удаления.", "error");
      return;
    }
    var name = state.selectedValueName || "";
    confirmAction("Удалить выбранное значение?" + "\n" + (name || "(По умолчанию)"))
      .then(function (ok) {
        if (!ok) {
          return;
        }
        fetchJson(config.valueDeleteUrl, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            path: state.currentPath,
            name: name,
            view: state.view,
          }),
        }).then(function (data) {
          if (!data.ok) {
            setStatus(data.error || "Не удалось удалить значение.", "error");
            return;
          }
          setStatus(data.message || "Значение удалено.", "success");
          state.selectedValueName = null;
          loadPath(state.currentPath);
        });
      });
  }

  function copyPath() {
    var path = state.currentPath || "";
    if (!path) {
      setStatus("Нет активного пути.", "warning");
      return;
    }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(path).then(function () {
        setStatus("Путь скопирован.", "success");
      });
    } else {
      setStatus("Скопируйте путь вручную.", "warning");
    }
  }

  function suggestExportName(path, isAll) {
    if (isAll) {
      return "registry_full_export_" + new Date().toISOString().replace(/[:.]/g, "-") + ".zip";
    }
    var base = (path || "registry").replace(/[\\/]+/g, "_");
    base = base.replace(/[^a-zA-Z0-9._-]/g, "_");
    base = base.replace(/_+/g, "_");
    if (!base) {
      base = "registry_branch";
    }
    return "registry_" + base + ".reg";
  }

  function setExportProgress(active, value) {
    var progress = $("reg-export-progress");
    if (!progress) {
      return;
    }
    progress.hidden = !active;
    if (active && typeof value === "number") {
      var bar = $("reg-export-progress-bar");
      var text = $("reg-export-progress-text");
      var pct = Math.max(0, Math.min(100, Math.round(value * 100)));
      if (bar) {
        bar.style.width = pct + "%";
      }
      if (text) {
        text.textContent = pct + "%";
      }
    }
  }

  function stopExportPolling() {
    if (exportProgressTimer) {
      clearTimeout(exportProgressTimer);
      exportProgressTimer = null;
    }
  }

  function pollFullExport(taskId) {
    if (!taskId || !config.exportFullStatusUrl) {
      setStatus("Не удалось отследить экспорт.", "error");
      setExportProgress(false, 0);
      return;
    }
    var url = config.exportFullStatusUrl.replace("__TASK__", encodeURIComponent(taskId));
    fetchJson(url).then(function (data) {
      if (!data.ok) {
        setExportProgress(false, 0);
        setStatus(data.error || "Ошибка статуса экспорта.", "error");
        return;
      }
      var status = data.status || "running";
      var progress = typeof data.progress === "number" ? data.progress : 0;
      setExportProgress(true, progress);
      if (status === "done") {
        setExportProgress(false, 1);
        setStatus("Экспорт завершен. Файл скачивается...", "success");
        if (data.download_url) {
          window.location.href = data.download_url;
        }
        return;
      }
      if (status === "error") {
        setExportProgress(false, 0);
        setStatus(data.error || "Экспорт завершился с ошибкой.", "error");
        return;
      }
      stopExportPolling();
      exportProgressTimer = setTimeout(function () {
        pollFullExport(taskId);
      }, 1000);
    });
  }

  function startFullExport(targetPath) {
    var filename = suggestExportName(targetPath, true);
    stopExportPolling();
    setExportProgress(true, 0);
    setStatus("Экспорт полного реестра...", "warning");
    fetchJson(config.exportFullStartUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ view: state.view, filename: filename }),
    }).then(function (data) {
      if (!data.ok) {
        setExportProgress(false, 0);
        setStatus(data.error || "Не удалось запустить экспорт.", "error");
        return;
      }
      pollFullExport(data.task_id);
    });
  }

  function exportRegistry(mode) {
    var targetPath = state.selectedKeyPath || state.currentPath || config.defaultRoot || "";
    if (mode === "branch" && !targetPath) {
      setStatus("Выберите ключ для экспорта.", "error");
      return;
    }
    if (mode === "all") {
      startFullExport(targetPath);
      return;
    }
    var filename = suggestExportName(targetPath, false);
    var url =
      config.exportUrl +
      "?path=" +
      encodeURIComponent(targetPath) +
      "&view=" +
      encodeURIComponent(state.view) +
      "&mode=" +
      encodeURIComponent("branch") +
      "&filename=" +
      encodeURIComponent(filename);
    window.location.href = url;
  }

  function importRegistryFile(file) {
    if (!file) {
      setStatus("Файл не выбран.", "error");
      return;
    }
    var form = new FormData();
    form.append("file", file);
    setStatus("Импорт...", "warning");
    fetchJson(config.importUrl, {
      method: "POST",
      body: form,
    }).then(function (data) {
      if (!data.ok) {
        setStatus(data.error || "Не удалось импортировать файл.", "error");
        return;
      }
      setStatus(data.message || "Импорт завершен.", "success");
      loadPath(state.currentPath || config.defaultRoot || "");
    });
  }

  function runSearch() {
    var query = ($("reg-search-query").value || "").trim();
    if (!query) {
      setStatus("Введите запрос.", "error");
      return;
    }
    var path = state.currentPath || config.defaultRoot || "";
    var depth = parseInt($("reg-search-depth").value || "4", 10) || 4;
    var keys = $("reg-search-keys").checked ? "1" : "0";
    var values = $("reg-search-values").checked ? "1" : "0";
    var data = $("reg-search-data").checked ? "1" : "0";
    var results = $("reg-search-results");
    results.innerHTML = "Поиск...";
    fetchJson(
      config.searchUrl +
        "?path=" +
        encodeURIComponent(path) +
        "&query=" +
        encodeURIComponent(query) +
        "&view=" +
        encodeURIComponent(state.view) +
        "&depth=" +
        encodeURIComponent(depth) +
        "&keys=" +
        keys +
        "&values=" +
        values +
        "&data=" +
        data
    ).then(function (dataResp) {
      if (!dataResp.ok) {
        results.innerHTML = dataResp.error || "Ошибка поиска.";
        return;
      }
      var payload = dataResp.data || {};
      var list = payload.results || [];
      results.innerHTML = "";
      if (!list.length) {
        results.textContent = "Ничего не найдено.";
        return;
      }
      list.forEach(function (item) {
        var card = document.createElement("div");
        card.className = "reg-search-item";
        var title = document.createElement("strong");
        title.textContent = item.kind === "key" ? "Ключ" : "Значение";
        var pathBtn = document.createElement("button");
        pathBtn.type = "button";
        pathBtn.textContent = item.path;
        pathBtn.addEventListener("click", function () {
          loadPath(item.path);
        });
        card.appendChild(title);
        card.appendChild(pathBtn);
        if (item.display_name) {
          var valueLine = document.createElement("span");
          valueLine.textContent = item.display_name;
          card.appendChild(valueLine);
        }
        results.appendChild(card);
      });
      if (payload.truncated) {
        var note = document.createElement("div");
        note.className = "muted";
        note.textContent = "Результаты усечены, уточните запрос.";
        results.appendChild(note);
      }
    });
  }

  function applyWritePermissions() {
    if (config.canWrite) {
      return;
    }
    document.querySelectorAll("[data-write-action]").forEach(function (btn) {
      btn.disabled = true;
      btn.classList.add("disabled");
    });
  }

  function initViewSelect() {
    var select = $("reg-view");
    if (!select) {
      return;
    }
    select.value = state.view || "default";
    select.addEventListener("change", function () {
      state.view = select.value || "default";
      if (state.currentPath) {
        loadPath(state.currentPath);
      } else {
        renderRoots();
      }
    });
  }

  function attachEvents() {
    $("reg-go").addEventListener("click", function () {
      var path = $("reg-path-input").value;
      loadPath(path);
    });
    $("reg-up").addEventListener("click", function () {
      if (!state.currentPath) {
        return;
      }
      var parts = state.currentPath.split("\\");
      parts.pop();
      loadPath(parts.join("\\"));
    });
    $("reg-root").addEventListener("click", function () {
      loadPath("");
    });
    $("reg-refresh").addEventListener("click", function () {
      loadPath(state.currentPath || "");
    });
    $("reg-copy").addEventListener("click", copyPath);
    $("reg-search-button").addEventListener("click", runSearch);

    $("reg-export-branch").addEventListener("click", function () {
      exportRegistry("branch");
    });
    $("reg-export-all").addEventListener("click", function () {
      confirmAction("Экспортировать весь реестр? Это может занять время и создать большой файл.")
        .then(function (ok) {
          if (ok) {
            exportRegistry("all");
          }
        });
    });

    $("reg-import").addEventListener("click", function () {
      if (!config.canWrite) {
        setStatus("Недостаточно прав для импорта.", "error");
        return;
      }
      var input = $("reg-import-input");
      if (input) {
        input.value = "";
        input.click();
      }
    });

    var importInput = $("reg-import-input");
    if (importInput) {
      importInput.addEventListener("change", function () {
        var file = importInput.files && importInput.files[0];
        if (!file) {
          return;
        }
        confirmAction("Импортировать выбранный .reg файл? Это изменит реестр.")
          .then(function (ok) {
            if (!ok) {
              return;
            }
            importRegistryFile(file);
          });
      });
    }

    $("reg-new-key").addEventListener("click", openKeyModal);
    $("reg-key-confirm").addEventListener("click", createKey);

    $("reg-rename-key").addEventListener("click", openRenameModal);
    $("reg-rename-confirm").addEventListener("click", renameKey);

    $("reg-delete-key").addEventListener("click", deleteKey);

    $("reg-new-value").addEventListener("click", function () {
      openValueModal(null);
    });
    $("reg-edit-value").addEventListener("click", function () {
      if (state.selectedValueName === null) {
        setStatus("Выберите значение.", "error");
        return;
      }
      openValueModal(state.selectedValueName);
    });
    $("reg-delete-value").addEventListener("click", deleteValue);

    $("reg-value-type").addEventListener("change", function (event) {
      var help = $("reg-value-help");
      if (help) {
        help.textContent = valueHelpText(event.target.value);
      }
    });
    $("reg-value-confirm").addEventListener("click", saveValue);
  }

  onReady(function () {
    if (!config.available) {
      setStatus(config.message || "Реестр недоступен.", "error");
    }
    applyWritePermissions();
    setupModalClose();
    initViewSelect();
    attachEvents();
    var startPath = config.defaultRoot || "";
    loadPath(startPath);
  });
})();
