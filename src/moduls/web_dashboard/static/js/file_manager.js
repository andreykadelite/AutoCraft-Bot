(function () {
  const config = window.FileManagerConfig || null;
  if (!config) {
    return;
  }

  const csrfToken = config.csrfToken || "";
  const TASK_POLL_INTERVAL_MS = 1000;

  const state = {
    currentPath: "",
    selected: new Set(),
    clipboard: null,
    operations: new Map(),
    operationCounter: 0,
  };

  const elements = {
    currentPath: document.getElementById("fm-current-path"),
    selection: document.getElementById("fm-selection"),
    pathInput: document.getElementById("fm-path-input"),
    quickLinks: document.getElementById("fm-quick-links"),
    breadcrumbs: document.getElementById("fm-breadcrumbs"),
    tableBody: document.getElementById("fm-table-body"),
    selectAll: document.getElementById("fm-select-all"),
    status: document.getElementById("fm-status"),
    clipboard: document.getElementById("fm-clipboard"),
    dropzone: document.getElementById("fm-dropzone"),
    progressList: document.getElementById("fm-progress-list"),
    progressEmpty: document.getElementById("fm-progress-empty"),
    clearProgress: document.getElementById("fm-clear-progress"),
    opsOverview: document.getElementById("fm-ops-overview"),
    opsOverviewTitle: document.getElementById("fm-ops-overview-title"),
    opsOverviewPercent: document.getElementById("fm-ops-overview-percent"),
    opsOverviewFill: document.getElementById("fm-ops-overview-fill"),
    uploadInput: document.getElementById("fm-upload-input"),
    modalCreate: document.getElementById("fm-modal-create"),
    modalRename: document.getElementById("fm-modal-rename"),
    modalProps: document.getElementById("fm-modal-props"),
    createInput: document.getElementById("fm-create-input"),
    renameInput: document.getElementById("fm-rename-input"),
    propsList: document.getElementById("fm-props-list"),
  };

  let lastFocused = null;

  function clamp(value, min, max) {
    return Math.min(max, Math.max(min, value));
  }

  function formatBytes(bytes) {
    if (bytes === null || bytes === undefined || Number.isNaN(bytes)) return "-";
    if (bytes <= 0) return "0 Б";
    const units = ["Б", "КБ", "МБ", "ГБ", "ТБ"];
    let value = bytes;
    let index = 0;
    while (value >= 1024 && index < units.length - 1) {
      value /= 1024;
      index += 1;
    }
    const fixed = value >= 100 ? 0 : value >= 10 ? 1 : 2;
    return `${value.toFixed(fixed)} ${units[index]}`;
  }

  function formatPercent(value) {
    const safe = clamp(Number(value) || 0, 0, 100);
    return `${safe.toFixed(safe >= 10 ? 0 : 1)}%`;
  }

  function formatDuration(seconds) {
    if (!Number.isFinite(seconds) || seconds < 0) {
      return "-";
    }
    const total = Math.round(seconds);
    const hh = Math.floor(total / 3600);
    const mm = Math.floor((total % 3600) / 60);
    const ss = total % 60;
    if (hh > 0) return `${hh}ч ${String(mm).padStart(2, "0")}м ${String(ss).padStart(2, "0")}с`;
    if (mm > 0) return `${mm}м ${String(ss).padStart(2, "0")}с`;
    return `${ss}с`;
  }

  function setStatus(message, type) {
    if (!elements.status) return;
    elements.status.textContent = message || "";
    elements.status.className = `fm-status ${type ? "fm-status-" + type : ""}`;
  }

  function updateSelectionBadge() {
    if (!elements.selection) return;
    elements.selection.textContent = `Выбрано: ${state.selected.size}`;
  }

  function setRowSelected(row, selected) {
    if (!row) return;
    row.classList.toggle("is-selected", !!selected);
  }

  function clearSelection() {
    state.selected.clear();
    if (elements.selectAll) {
      elements.selectAll.checked = false;
    }
    if (elements.tableBody) {
      elements.tableBody.querySelectorAll("tr").forEach((row) => setRowSelected(row, false));
      elements.tableBody.querySelectorAll("input[type='checkbox']").forEach((input) => {
        input.checked = false;
      });
    }
    updateSelectionBadge();
  }

  function anyModalOpen() {
    return [elements.modalCreate, elements.modalRename, elements.modalProps].some((modal) => modal && !modal.hidden);
  }

  function openModal(modal) {
    if (!modal) return;
    lastFocused = document.activeElement;
    modal.hidden = false;
    document.body.classList.add("modal-open");
    const input = modal.querySelector("input");
    if (input) {
      setTimeout(() => input.focus(), 0);
    }
  }

  function closeModal(modal) {
    if (!modal) return;
    modal.hidden = true;
    if (!anyModalOpen()) {
      document.body.classList.remove("modal-open");
    }
    if (lastFocused && typeof lastFocused.focus === "function") {
      lastFocused.focus();
    }
  }

  async function confirmAction(message, options) {
    if (window.panelConfirm) {
      return window.panelConfirm(message, options || {});
    }
    return window.confirm(message);
  }

  function bindModalClose(modal) {
    if (!modal) return;
    modal.addEventListener("click", (event) => {
      const target = event.target;
      if (target === modal) {
        closeModal(modal);
        return;
      }
      if (target && target.hasAttribute("data-modal-close")) {
        closeModal(modal);
      }
    });
  }

  bindModalClose(elements.modalCreate);
  bindModalClose(elements.modalRename);
  bindModalClose(elements.modalProps);

  function renderQuickLinks(links) {
    elements.quickLinks.innerHTML = "";
    if (!links || !links.length) {
      const empty = document.createElement("p");
      empty.className = "muted";
      empty.textContent = "Нет доступных ярлыков.";
      elements.quickLinks.appendChild(empty);
      return;
    }
    links.forEach((link) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "btn fm-link";
      button.setAttribute("role", "listitem");
      button.textContent = link.label;
      button.title = link.path;
      button.addEventListener("click", () => loadDirectory(link.path));
      elements.quickLinks.appendChild(button);
    });
  }

  function renderBreadcrumbs(items) {
    elements.breadcrumbs.innerHTML = "";
    if (!items || !items.length) {
      return;
    }
    items.forEach((crumb, index) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "fm-breadcrumb";
      button.textContent = crumb.label;
      button.addEventListener("click", () => loadDirectory(crumb.path));
      elements.breadcrumbs.appendChild(button);
      if (index < items.length - 1) {
        const divider = document.createElement("span");
        divider.className = "fm-breadcrumb-divider";
        divider.textContent = ">";
        elements.breadcrumbs.appendChild(divider);
      }
    });
  }

  function updateClipboard() {
    if (!elements.clipboard) return;
    if (!state.clipboard) {
      elements.clipboard.textContent = "";
      return;
    }
    const action = state.clipboard.mode === "move" ? "Вырезано" : "Скопировано";
    elements.clipboard.textContent = `${action}: ${state.clipboard.items.length} объект(ов).`;
  }

  function statusLabel(status) {
    if (status === "done") return "Завершено";
    if (status === "error") return "Ошибка";
    return "Выполняется";
  }

  function syncProgressEmptyState() {
    if (!elements.progressEmpty || !elements.progressList) return;
    elements.progressEmpty.hidden = elements.progressList.children.length > 0;
  }

  function updateOperationsOverview() {
    if (!elements.opsOverview || !elements.opsOverviewTitle || !elements.opsOverviewPercent || !elements.opsOverviewFill) {
      return;
    }
    const running = Array.from(state.operations.values()).filter((operation) => operation.status === "running");
    if (!running.length) {
      elements.opsOverview.hidden = true;
      elements.opsOverviewTitle.textContent = "Активные операции: 0";
      elements.opsOverviewPercent.textContent = "0%";
      elements.opsOverviewFill.style.width = "0%";
      return;
    }
    const avg = running.reduce((acc, operation) => acc + operation.progress, 0) / running.length;
    elements.opsOverview.hidden = false;
    elements.opsOverviewTitle.textContent = `Активные операции: ${running.length}`;
    elements.opsOverviewPercent.textContent = formatPercent(avg);
    elements.opsOverviewFill.style.width = `${clamp(avg, 0, 100)}%`;
  }

  function createOperation(label, kind) {
    const id = `${kind || "task"}-${Date.now()}-${++state.operationCounter}`;
    const startedAtMs = performance.now();

    const card = document.createElement("article");
    card.className = "fm-progress-item";

    const head = document.createElement("div");
    head.className = "fm-progress-head";

    const title = document.createElement("div");
    title.className = "fm-progress-title";
    title.textContent = label;

    const status = document.createElement("span");
    status.className = "fm-progress-status running";
    status.textContent = statusLabel("running");

    head.appendChild(title);
    head.appendChild(status);

    const track = document.createElement("div");
    track.className = "fm-progress-track";
    const fill = document.createElement("span");
    fill.className = "fm-progress-fill";
    fill.style.width = "0%";
    track.appendChild(fill);

    const meta = document.createElement("div");
    meta.className = "fm-progress-meta";

    const percent = document.createElement("span");
    percent.textContent = "0%";

    const speed = document.createElement("span");
    speed.textContent = "Скорость: -";

    const eta = document.createElement("span");
    eta.textContent = "Осталось: -";

    const elapsed = document.createElement("span");
    elapsed.textContent = "Время: 0с";

    meta.appendChild(percent);
    meta.appendChild(speed);
    meta.appendChild(eta);
    meta.appendChild(elapsed);

    const message = document.createElement("div");
    message.className = "fm-progress-message";

    const actions = document.createElement("div");
    actions.className = "fm-progress-actions";

    card.appendChild(head);
    card.appendChild(track);
    card.appendChild(meta);
    card.appendChild(message);
    card.appendChild(actions);

    elements.progressList.prepend(card);

    const operation = {
      id,
      kind: kind || "task",
      status: "running",
      progress: 0,
      startedAtMs,
      lastUpdateMs: startedAtMs,
      lastProgress: 0,
      smoothRate: 0,
      smoothSpeedBps: 0,
      card,
      fill,
      percent,
      speed,
      eta,
      elapsed,
      message,
      actions,
      statusEl: status,
    };

    state.operations.set(id, operation);
    syncProgressEmptyState();
    updateOperationsOverview();
    return operation;
  }

  function setOperationProgress(operation, progressValue, options) {
    if (!operation) return;
    const opts = options || {};
    const now = performance.now();
    const progress = clamp(Number(progressValue) || 0, 0, 100);
    const deltaProgress = Math.max(0, progress - operation.lastProgress);
    const deltaSec = Math.max((now - operation.lastUpdateMs) / 1000, 0.001);

    if (deltaProgress > 0) {
      const currentRate = deltaProgress / deltaSec;
      operation.smoothRate = operation.smoothRate ? operation.smoothRate * 0.75 + currentRate * 0.25 : currentRate;
    }

    if (typeof opts.speedBps === "number" && Number.isFinite(opts.speedBps) && opts.speedBps > 0) {
      operation.smoothSpeedBps = operation.smoothSpeedBps
        ? operation.smoothSpeedBps * 0.75 + opts.speedBps * 0.25
        : opts.speedBps;
    }

    operation.progress = progress;
    operation.lastProgress = progress;
    operation.lastUpdateMs = now;

    operation.fill.style.width = `${progress}%`;
    operation.percent.textContent = formatPercent(progress);

    let speedText = "-";
    if (opts.speedText) {
      speedText = opts.speedText;
    } else if (operation.smoothSpeedBps > 0) {
      speedText = `${formatBytes(operation.smoothSpeedBps)}/с`;
    } else if (operation.smoothRate > 0) {
      speedText = `${operation.smoothRate.toFixed(2)} %/с`;
    }
    operation.speed.textContent = `Скорость: ${speedText}`;

    let etaText = "-";
    if (typeof opts.etaSeconds === "number" && Number.isFinite(opts.etaSeconds) && opts.etaSeconds >= 0) {
      etaText = formatDuration(opts.etaSeconds);
    } else if (opts.etaText) {
      etaText = opts.etaText;
    } else if (operation.smoothRate > 0 && progress < 100) {
      etaText = formatDuration((100 - progress) / operation.smoothRate);
    }
    operation.eta.textContent = `Осталось: ${etaText}`;

    if (opts.message !== undefined) {
      operation.message.textContent = opts.message || "";
    }

    operation.elapsed.textContent = `Время: ${formatDuration((now - operation.startedAtMs) / 1000)}`;
    updateOperationsOverview();
  }

  function setOperationStatus(operation, status, options) {
    if (!operation) return;
    const opts = options || {};
    operation.status = status;
    operation.card.classList.remove("fm-progress-done", "fm-progress-error");

    if (status === "done") {
      operation.card.classList.add("fm-progress-done");
    } else if (status === "error") {
      operation.card.classList.add("fm-progress-error");
    }

    operation.statusEl.className = `fm-progress-status ${status === "error" ? "error" : status === "done" ? "done" : "running"}`;
    operation.statusEl.textContent = statusLabel(status);

    if (opts.message !== undefined) {
      operation.message.textContent = opts.message || "";
    }

    if (status === "done") {
      setOperationProgress(operation, 100, {
        message: opts.message || operation.message.textContent,
        speedText: operation.smoothSpeedBps > 0 ? `${formatBytes(operation.smoothSpeedBps)}/с` : undefined,
        etaText: "0с",
      });
    } else {
      updateOperationsOverview();
    }
  }

  function addOperationAction(operation, label, onClick) {
    if (!operation || !operation.actions) return;
    const button = document.createElement("button");
    button.type = "button";
    button.className = "btn";
    button.textContent = label;
    button.addEventListener("click", onClick);
    operation.actions.appendChild(button);
  }

  function clearFinishedOperations() {
    let removed = 0;
    for (const [key, operation] of state.operations.entries()) {
      if (operation.status === "running") {
        continue;
      }
      operation.card.remove();
      state.operations.delete(key);
      removed += 1;
    }
    syncProgressEmptyState();
    updateOperationsOverview();
    if (removed > 0) {
      setStatus(`Удалено завершённых задач: ${removed}.`, "success");
    }
  }

  function refreshRunningOperationMeta() {
    const now = performance.now();
    state.operations.forEach((operation) => {
      if (operation.status !== "running") return;
      operation.elapsed.textContent = `Время: ${formatDuration((now - operation.startedAtMs) / 1000)}`;
    });
    updateOperationsOverview();
  }

  function createTypeLabel(item) {
    if (item.is_drive) return "Диск";
    if (item.is_dir) return "Папка";
    return "Файл";
  }

  function createRow(item) {
    const row = document.createElement("tr");
    row.className = "fm-row";
    row.dataset.path = item.path;
    row.dataset.type = item.is_dir ? "dir" : "file";

    const selectCell = document.createElement("td");
    selectCell.dataset.label = "Выбор";
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.setAttribute("aria-label", `Выбрать ${item.name}`);
    checkbox.addEventListener("change", () => {
      if (checkbox.checked) {
        state.selected.add(item.path);
      } else {
        state.selected.delete(item.path);
      }
      setRowSelected(row, checkbox.checked);
      updateSelectionBadge();
    });
    selectCell.appendChild(checkbox);

    const nameCell = document.createElement("td");
    nameCell.dataset.label = "Имя";
    const nameWrap = document.createElement("div");
    nameWrap.className = "fm-name-wrap";

    const kind = document.createElement("span");
    kind.className = `fm-kind ${item.is_drive ? "drive" : item.is_dir ? "dir" : "file"}`;
    kind.textContent = createTypeLabel(item);

    const nameButton = document.createElement("button");
    nameButton.type = "button";
    nameButton.className = "fm-name-btn";
    nameButton.title = item.path || item.name;
    nameButton.textContent = item.is_drive ? item.path : item.name;
    nameButton.addEventListener("click", () => {
      if (item.is_dir || item.is_drive) {
        loadDirectory(item.path);
      } else {
        checkbox.checked = !checkbox.checked;
        checkbox.dispatchEvent(new Event("change"));
      }
    });

    nameWrap.appendChild(kind);
    nameWrap.appendChild(nameButton);
    nameCell.appendChild(nameWrap);

    const typeCell = document.createElement("td");
    typeCell.dataset.label = "Тип";
    typeCell.textContent = createTypeLabel(item);

    const sizeCell = document.createElement("td");
    sizeCell.dataset.label = "Размер";
    if (item.is_drive && item.size) {
      const free = item.free ? formatBytes(item.free) : "-";
      sizeCell.textContent = `${formatBytes(item.size)} (свободно ${free})`;
    } else if (item.is_file) {
      sizeCell.textContent = formatBytes(item.size);
    } else {
      sizeCell.textContent = "-";
    }

    const modCell = document.createElement("td");
    modCell.dataset.label = "Изменено";
    modCell.textContent = item.modified || "-";

    const actionsCell = document.createElement("td");
    actionsCell.dataset.label = "Действия";
    actionsCell.className = "fm-row-actions";

    if (item.is_dir || item.is_drive) {
      const openBtn = document.createElement("button");
      openBtn.type = "button";
      openBtn.className = "btn";
      openBtn.textContent = "Открыть";
      openBtn.addEventListener("click", () => loadDirectory(item.path));
      actionsCell.appendChild(openBtn);
    } else {
      const downloadBtn = document.createElement("button");
      downloadBtn.type = "button";
      downloadBtn.className = "btn";
      downloadBtn.textContent = "Скачать";
      downloadBtn.addEventListener("click", () => downloadSingle(item.path));
      actionsCell.appendChild(downloadBtn);
    }

    row.appendChild(selectCell);
    row.appendChild(nameCell);
    row.appendChild(typeCell);
    row.appendChild(sizeCell);
    row.appendChild(modCell);
    row.appendChild(actionsCell);
    return row;
  }

  function renderItems(items) {
    elements.tableBody.innerHTML = "";
    if (!items || !items.length) {
      const row = document.createElement("tr");
      row.className = "fm-row-empty";
      const cell = document.createElement("td");
      cell.colSpan = 6;
      cell.textContent = "Нет файлов для отображения.";
      row.appendChild(cell);
      elements.tableBody.appendChild(row);
      return;
    }
    items.forEach((item) => {
      elements.tableBody.appendChild(createRow(item));
    });
  }

  function getSelectedPaths() {
    return Array.from(state.selected.values());
  }

  async function parseJsonResponse(response) {
    const text = await response.text();
    try {
      return JSON.parse(text);
    } catch (err) {
      return { ok: false, error: "Неверный ответ сервера." };
    }
  }

  async function postJson(url, payload) {
    try {
      const headers = { "Content-Type": "application/json" };
      if (csrfToken) {
        headers["X-CSRFToken"] = csrfToken;
      }
      const response = await fetch(url, {
        method: "POST",
        headers,
        body: JSON.stringify(payload || {}),
      });
      return await parseJsonResponse(response);
    } catch (err) {
      return { ok: false, error: `Ошибка запроса: ${err}` };
    }
  }

  async function loadDirectory(path) {
    setStatus("Загрузка каталога...", "");
    clearSelection();
    state.currentPath = path || "";

    const url = config.listUrl + (path ? `?path=${encodeURIComponent(path)}` : "");

    try {
      const response = await fetch(url);
      const data = await parseJsonResponse(response);
      if (!data.ok) {
        setStatus(data.error || "Не удалось получить список файлов.", "error");
        return;
      }

      state.currentPath = data.current || "";
      elements.currentPath.textContent = data.display || "Корень";
      elements.pathInput.value = data.current || "";
      renderQuickLinks(data.quick_links || []);
      renderBreadcrumbs(data.breadcrumbs || []);
      renderItems(data.items || []);
      setStatus("", "");
    } catch (err) {
      setStatus(`Ошибка загрузки: ${err}`, "error");
    }
  }

  async function createFolder() {
    const name = (elements.createInput.value || "").trim();
    if (!name) {
      setStatus("Укажите имя папки.", "warning");
      return;
    }

    const data = await postJson(config.mkdirUrl, { path: state.currentPath, name });
    if (data.ok) {
      closeModal(elements.modalCreate);
      elements.createInput.value = "";
      await loadDirectory(state.currentPath);
      setStatus(data.message || "Папка создана.", "success");
      return;
    }

    setStatus(data.error || "Не удалось создать папку.", "error");
  }

  async function renameItem() {
    const paths = getSelectedPaths();
    if (paths.length !== 1) {
      setStatus("Выберите один файл или папку для переименования.", "warning");
      return;
    }

    const newName = (elements.renameInput.value || "").trim();
    if (!newName) {
      setStatus("Введите новое имя.", "warning");
      return;
    }

    const data = await postJson(config.renameUrl, { path: paths[0], new_name: newName });
    if (data.ok) {
      closeModal(elements.modalRename);
      elements.renameInput.value = "";
      await loadDirectory(state.currentPath);
      setStatus(data.message || "Переименование выполнено.", "success");
      return;
    }

    setStatus(data.error || "Не удалось переименовать.", "error");
  }

  function startTask(taskId, label, onDone) {
    if (!taskId) return;

    const operation = createOperation(label, "task");
    setOperationProgress(operation, 0, { message: "Запуск операции..." });

    async function poll() {
      try {
        const response = await fetch(config.taskUrl.replace("__TASK__", taskId));
        const data = await parseJsonResponse(response);

        if (!data.ok) {
          setOperationStatus(operation, "error", { message: data.error || "Задача не найдена." });
          return;
        }

        const task = data.task || {};
        setOperationProgress(operation, Number(task.progress) || 0, {
          message: task.message || "",
        });

        if (task.status === "running") {
          setTimeout(poll, TASK_POLL_INTERVAL_MS);
          return;
        }

        if (task.status === "error") {
          setOperationStatus(operation, "error", {
            message: task.error || task.message || "Ошибка выполнения операции.",
          });
        } else {
          setOperationStatus(operation, "done", {
            message: task.message || "Операция завершена.",
          });
        }

        if (data.download_url) {
          addOperationAction(operation, "Скачать архив", () => {
            window.open(data.download_url, "_blank");
          });
        }

        if (typeof onDone === "function") {
          onDone(task, data.download_url || "");
        }
      } catch (err) {
        setOperationStatus(operation, "error", { message: `Ошибка получения статуса: ${err}` });
      }
    }

    poll();
  }

  async function deleteSelected() {
    const paths = getSelectedPaths();
    if (!paths.length) {
      setStatus("Выберите файлы или папки для удаления.", "warning");
      return;
    }

    const ok = await confirmAction("Удалить выбранные элементы?", { danger: true });
    if (!ok) return;

    const data = await postJson(config.deleteUrl, { paths });
    if (!data.ok) {
      setStatus(data.error || "Не удалось удалить.", "error");
      return;
    }

    startTask(data.task_id, "Удаление файлов", () => {
      loadDirectory(state.currentPath);
    });
  }

  function copyOrMove(mode) {
    const paths = getSelectedPaths();
    if (!paths.length) {
      setStatus("Выберите файлы или папки.", "warning");
      return;
    }

    state.clipboard = { mode, items: paths.slice() };
    updateClipboard();
    setStatus(mode === "move" ? "Элементы вырезаны." : "Элементы скопированы.", "success");
  }

  async function pasteClipboard() {
    if (!state.clipboard || !state.clipboard.items.length) {
      setStatus("Буфер пуст.", "warning");
      return;
    }

    if (!state.currentPath) {
      setStatus("Откройте целевую папку для вставки.", "warning");
      return;
    }

    if (state.clipboard.mode === "move") {
      const ok = await confirmAction("Переместить выбранные элементы?", { danger: true });
      if (!ok) return;
    }

    const payload = {
      paths: state.clipboard.items,
      destination: state.currentPath,
    };

    const url = state.clipboard.mode === "move" ? config.moveUrl : config.copyUrl;
    const data = await postJson(url, payload);
    if (!data.ok) {
      setStatus(data.error || "Не удалось выполнить операцию.", "error");
      return;
    }

    startTask(
      data.task_id,
      state.clipboard.mode === "move" ? "Перемещение файлов" : "Копирование файлов",
      () => {
        loadDirectory(state.currentPath);
      }
    );

    state.clipboard = null;
    updateClipboard();
  }
  async function downloadSelected() {
    const paths = getSelectedPaths();
    if (!paths.length) {
      setStatus("Выберите файл или папку для скачивания.", "warning");
      return;
    }

    const data = await postJson(config.prepareDownloadUrl, { paths });
    if (!data.ok) {
      setStatus(data.error || "Не удалось подготовить скачивание.", "error");
      return;
    }

    if (data.download_url) {
      window.open(data.download_url, "_blank");
      return;
    }

    startTask(data.task_id, "Подготовка архива", () => {
      loadDirectory(state.currentPath);
    });
  }

  function downloadSingle(path) {
    const url = `${config.downloadUrl}?path=${encodeURIComponent(path)}`;
    window.open(url, "_blank");
  }

  async function showProperties() {
    const paths = getSelectedPaths();
    if (paths.length !== 1) {
      setStatus("Выберите один файл или папку для просмотра свойств.", "warning");
      return;
    }

    try {
      const response = await fetch(`${config.propertiesUrl}?path=${encodeURIComponent(paths[0])}`);
      const data = await parseJsonResponse(response);
      if (!data.ok) {
        setStatus(data.error || "Не удалось получить свойства.", "error");
        return;
      }
      renderProperties(data.data || {});
      openModal(elements.modalProps);
    } catch (err) {
      setStatus(`Ошибка: ${err}`, "error");
    }
  }

  function renderProperties(props) {
    elements.propsList.innerHTML = "";

    function add(label, value) {
      const dt = document.createElement("dt");
      dt.textContent = label;
      const dd = document.createElement("dd");
      dd.textContent = value;
      elements.propsList.appendChild(dt);
      elements.propsList.appendChild(dd);
    }

    add("Имя", props.name || "-");
    add("Путь", props.path || "-");
    add("Тип", props.is_dir ? "Папка" : "Файл");

    if (props.is_file) {
      add("Размер", formatBytes(props.size || 0));
    }

    if (props.is_dir) {
      if (props.drive) {
        add("Всего", formatBytes(props.total_bytes || 0));
        add("Свободно", formatBytes(props.free_bytes || 0));
        add("Использовано", formatBytes(props.used_bytes || 0));
      } else {
        add("Размер (всего)", formatBytes(props.total_bytes || 0));
        add("Файлов", String(props.total_files || 0));
        add("Папок", String(props.total_dirs || 0));
        if (props.truncated) {
          add("Примечание", "Подсчет ограничен для больших каталогов.");
        }
      }
    }

    add("Создано", props.created || "-");
    add("Изменено", props.modified || "-");
    add("Доступ на чтение", props.readable ? "Да" : "Нет");
    add("Доступ на запись", props.writable ? "Да" : "Нет");
  }

  function safeJsonParse(text) {
    try {
      return JSON.parse(text);
    } catch (err) {
      return null;
    }
  }

  function uploadFiles(fileList) {
    const files = Array.from(fileList || []);
    if (!files.length) {
      return;
    }

    if (!state.currentPath) {
      setStatus("Откройте папку перед загрузкой файлов.", "warning");
      return;
    }

    const totalBytes = files.reduce((acc, file) => acc + (Number(file.size) || 0), 0);
    const operation = createOperation(`Загрузка (${files.length})`, "upload");
    setOperationProgress(operation, 0, { message: "Подготовка файлов к отправке..." });

    const form = new FormData();
    files.forEach((file) => {
      form.append("files", file);
    });

    const xhr = new XMLHttpRequest();
    const url = `${config.uploadUrl}?path=${encodeURIComponent(state.currentPath)}`;

    let completed = false;
    let lastLoaded = 0;
    let lastTs = performance.now();
    let smoothSpeed = 0;

    function finalize(status, message, data) {
      if (completed) {
        return;
      }
      completed = true;

      if (status === "done") {
        setOperationProgress(operation, 100, {
          message,
          speedText: smoothSpeed > 0 ? `${formatBytes(smoothSpeed)}/с` : undefined,
          etaText: "0с",
        });
        setOperationStatus(operation, "done", { message });
        setStatus(message || "Файлы загружены.", "success");
        loadDirectory(state.currentPath);
        return;
      }

      setOperationStatus(operation, "error", { message });
      setStatus(message || "Ошибка загрузки файлов.", "error");

      if (data && data.details && Array.isArray(data.details) && data.details.length) {
        operation.message.textContent = `${message}\n${data.details.join("\n")}`;
      }
    }

    xhr.upload.addEventListener("progress", (event) => {
      if (!event.lengthComputable) {
        return;
      }

      const now = performance.now();
      const loaded = Number(event.loaded) || 0;
      const total = Number(event.total) || totalBytes || 1;
      const deltaBytes = Math.max(0, loaded - lastLoaded);
      const deltaSec = Math.max((now - lastTs) / 1000, 0.001);
      const currentSpeed = deltaBytes / deltaSec;
      smoothSpeed = smoothSpeed ? smoothSpeed * 0.7 + currentSpeed * 0.3 : currentSpeed;

      lastLoaded = loaded;
      lastTs = now;

      const progress = clamp((loaded / total) * 100, 0, 99.5);
      const remaining = Math.max(0, total - loaded);
      const etaSeconds = smoothSpeed > 0 ? remaining / smoothSpeed : undefined;

      setOperationProgress(operation, progress, {
        message: `Отправлено ${formatBytes(loaded)} из ${formatBytes(total)}.`,
        speedBps: smoothSpeed,
        etaSeconds,
      });
    });

    xhr.upload.addEventListener("load", () => {
      setOperationProgress(operation, 99.5, {
        message: "Файлы отправлены. Ожидается подтверждение от сервера...",
        speedText: smoothSpeed > 0 ? `${formatBytes(smoothSpeed)}/с` : "-",
      });
    });

    xhr.addEventListener("error", () => {
      finalize("error", "Ошибка сети при загрузке файлов.");
    });

    xhr.addEventListener("abort", () => {
      finalize("error", "Загрузка была прервана.");
    });

    xhr.onreadystatechange = () => {
      if (xhr.readyState !== 4) {
        return;
      }

      const data = safeJsonParse(xhr.responseText);
      if (xhr.status >= 200 && xhr.status < 300 && data && data.ok) {
        finalize("done", data.message || "Файлы загружены.", data);
        return;
      }

      const error = (data && data.error) || `Ошибка загрузки (${xhr.status}).`;
      finalize("error", error, data);
    };

    xhr.open("POST", url, true);
    if (csrfToken) {
      xhr.setRequestHeader("X-CSRFToken", csrfToken);
    }
    xhr.send(form);
  }

  function setupDropzone() {
    if (!elements.dropzone) return;

    const stop = (event) => {
      event.preventDefault();
      event.stopPropagation();
    };

    ["dragenter", "dragover"].forEach((eventName) => {
      elements.dropzone.addEventListener(eventName, (event) => {
        stop(event);
        elements.dropzone.classList.add("is-dragover");
      });
    });

    ["dragleave", "dragend", "drop"].forEach((eventName) => {
      elements.dropzone.addEventListener(eventName, (event) => {
        stop(event);
        elements.dropzone.classList.remove("is-dragover");
      });
    });

    elements.dropzone.addEventListener("drop", (event) => {
      const transfer = event.dataTransfer;
      if (!transfer || !transfer.files || !transfer.files.length) {
        return;
      }
      uploadFiles(transfer.files);
    });

    elements.dropzone.addEventListener("click", () => {
      elements.uploadInput.value = "";
      elements.uploadInput.click();
    });

    elements.dropzone.addEventListener("keydown", (event) => {
      if (event.key !== "Enter" && event.key !== " ") {
        return;
      }
      event.preventDefault();
      elements.uploadInput.value = "";
      elements.uploadInput.click();
    });
  }

  function getParentPath(path) {
    if (!path) return "";

    const normalized = path.replace(/[\\/]+$/, "");
    if (!normalized) {
      return "";
    }

    if (normalized.startsWith("\\\\")) {
      const parts = normalized.split("\\").filter((part) => part !== "");
      if (parts.length <= 2) {
        return "";
      }
      parts.pop();
      return `\\\\${parts.join("\\")}`;
    }

    const separator = normalized.includes("\\") ? "\\" : "/";
    const parts = normalized.split(/[\\/]/).filter((part) => part !== "");
    if (parts.length <= 1) {
      return "";
    }

    parts.pop();
    let parent = parts.join(separator);

    if (/^[A-Za-z]:$/.test(parent)) {
      parent += "\\";
    }

    return parent;
  }
  document.getElementById("fm-refresh").addEventListener("click", () => loadDirectory(state.currentPath));
  document.getElementById("fm-root").addEventListener("click", () => loadDirectory(""));
  document.getElementById("fm-up").addEventListener("click", () => {
    if (!state.currentPath) {
      loadDirectory("");
      return;
    }
    loadDirectory(getParentPath(state.currentPath));
  });

  document.getElementById("fm-go").addEventListener("click", () => {
    loadDirectory((elements.pathInput.value || "").trim());
  });

  elements.pathInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      loadDirectory((elements.pathInput.value || "").trim());
    }
  });

  document.getElementById("fm-new-folder").addEventListener("click", () => {
    elements.createInput.value = "";
    openModal(elements.modalCreate);
  });
  document.getElementById("fm-create-confirm").addEventListener("click", createFolder);

  document.getElementById("fm-rename").addEventListener("click", () => {
    const paths = getSelectedPaths();
    if (paths.length !== 1) {
      setStatus("Выберите один файл или папку для переименования.", "warning");
      return;
    }
    const parts = paths[0].split(/[\\/]/);
    elements.renameInput.value = parts[parts.length - 1] || "";
    openModal(elements.modalRename);
  });
  document.getElementById("fm-rename-confirm").addEventListener("click", renameItem);

  document.getElementById("fm-delete").addEventListener("click", deleteSelected);
  document.getElementById("fm-copy").addEventListener("click", () => copyOrMove("copy"));
  document.getElementById("fm-cut").addEventListener("click", () => copyOrMove("move"));
  document.getElementById("fm-paste").addEventListener("click", pasteClipboard);
  document.getElementById("fm-download").addEventListener("click", downloadSelected);
  document.getElementById("fm-properties").addEventListener("click", showProperties);

  document.getElementById("fm-upload").addEventListener("click", () => {
    elements.uploadInput.value = "";
    elements.uploadInput.click();
  });

  elements.uploadInput.addEventListener("change", (event) => {
    uploadFiles(event.target.files);
  });

  elements.selectAll.addEventListener("change", () => {
    const checked = elements.selectAll.checked;
    state.selected.clear();

    elements.tableBody.querySelectorAll("tr").forEach((row) => {
      const input = row.querySelector("input[type='checkbox']");
      if (!input) {
        return;
      }
      input.checked = checked;
      if (checked && row.dataset.path) {
        state.selected.add(row.dataset.path);
      }
      setRowSelected(row, checked);
    });

    updateSelectionBadge();
  });

  if (elements.clearProgress) {
    elements.clearProgress.addEventListener("click", clearFinishedOperations);
  }

  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    [elements.modalCreate, elements.modalRename, elements.modalProps].forEach((modal) => {
      if (modal && !modal.hidden) {
        closeModal(modal);
      }
    });
  });

  setupDropzone();
  syncProgressEmptyState();
  updateOperationsOverview();
  setInterval(refreshRunningOperationMeta, 1000);

  loadDirectory("");
})();
