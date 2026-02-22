(function () {
  const config = window.FileManagerConfig || null;
  if (!config) {
    return;
  }

  const csrfToken = config.csrfToken || "";
  const state = {
    currentPath: "",
    selected: new Set(),
    clipboard: null,
    busy: false,
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
    progressList: document.getElementById("fm-progress-list"),
    uploadInput: document.getElementById("fm-upload-input"),
    modalCreate: document.getElementById("fm-modal-create"),
    modalRename: document.getElementById("fm-modal-rename"),
    modalProps: document.getElementById("fm-modal-props"),
    createInput: document.getElementById("fm-create-input"),
    renameInput: document.getElementById("fm-rename-input"),
    propsList: document.getElementById("fm-props-list"),
  };

  let lastFocused = null;

  function formatBytes(bytes) {
    if (bytes === null || bytes === undefined) return "-";
    if (bytes === 0) return "0 Б";
    const units = ["Б", "КБ", "МБ", "ГБ", "ТБ"];
    let index = 0;
    let value = bytes;
    while (value >= 1024 && index < units.length - 1) {
      value /= 1024;
      index += 1;
    }
    return `${value.toFixed(value >= 10 ? 0 : 1)} ${units[index]}`;
  }

  function setStatus(message, type) {
    if (!elements.status) return;
    elements.status.textContent = message || "";
    elements.status.className = `fm-status ${type ? "fm-status-" + type : ""}`;
  }

  function updateSelectionBadge() {
    elements.selection.textContent = `Выбрано: ${state.selected.size}`;
  }

  function clearSelection() {
    state.selected.clear();
    updateSelectionBadge();
    if (elements.selectAll) {
      elements.selectAll.checked = false;
    }
  }

  function openModal(modal) {
    if (!modal) return;
    lastFocused = document.activeElement;
    modal.hidden = false;
    const input = modal.querySelector("input");
    if (input) {
      setTimeout(() => input.focus(), 0);
    }
  }

  function closeModal(modal) {
    if (!modal) return;
    modal.hidden = true;
    if (lastFocused) {
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
    if (!state.clipboard) {
      elements.clipboard.textContent = "";
      return;
    }
    const action = state.clipboard.mode === "move" ? "Вырезано" : "Скопировано";
    elements.clipboard.textContent = `${action}: ${state.clipboard.items.length} объект(ов).`;
  }

  function createRow(item) {
    const row = document.createElement("tr");
    row.dataset.path = item.path;
    row.dataset.type = item.is_dir ? "dir" : "file";

    const selectCell = document.createElement("td");
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.setAttribute("aria-label", `Выбрать ${item.name}`);
    checkbox.addEventListener("change", () => {
      if (checkbox.checked) {
        state.selected.add(item.path);
      } else {
        state.selected.delete(item.path);
      }
      updateSelectionBadge();
    });
    selectCell.appendChild(checkbox);

    const nameCell = document.createElement("td");
    const nameButton = document.createElement("button");
    nameButton.type = "button";
    nameButton.className = "fm-name-btn";
    nameButton.textContent = item.is_drive ? item.path : item.name;
    nameButton.addEventListener("click", () => {
      if (item.is_dir || item.is_drive) {
        loadDirectory(item.path);
      } else {
        checkbox.checked = !checkbox.checked;
        checkbox.dispatchEvent(new Event("change"));
      }
    });
    nameCell.appendChild(nameButton);

    const typeCell = document.createElement("td");
    if (item.is_drive) {
      typeCell.textContent = "Диск";
    } else if (item.is_dir) {
      typeCell.textContent = "Папка";
    } else {
      typeCell.textContent = "Файл";
    }

    const sizeCell = document.createElement("td");
    if (item.is_drive && item.size) {
      const free = item.free ? formatBytes(item.free) : "-";
      sizeCell.textContent = `${formatBytes(item.size)} (свободно ${free})`;
    } else if (item.is_file) {
      sizeCell.textContent = formatBytes(item.size);
    } else {
      sizeCell.textContent = "-";
    }

    const modCell = document.createElement("td");
    modCell.textContent = item.modified || "-";

    const actionsCell = document.createElement("td");
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

  async function loadDirectory(path) {
    setStatus("Загрузка каталога...", "");
    clearSelection();
    state.currentPath = path || "";
    const url = config.listUrl + (path ? `?path=${encodeURIComponent(path)}` : "");
    try {
      const response = await fetch(url);
      const data = await response.json();
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

  function renderItems(items) {
    elements.tableBody.innerHTML = "";
    if (!items || !items.length) {
      const row = document.createElement("tr");
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
      const data = await response.json();
      if (!data || typeof data.ok === "undefined") {
        return { ok: false, error: "Неверный ответ сервера." };
      }
      return data;
    } catch (err) {
      return { ok: false, error: `Ошибка запроса: ${err}` };
    }
  }

  async function createFolder() {
    const name = elements.createInput.value.trim();
    if (!name) {
      setStatus("Укажите имя папки.", "warning");
      return;
    }
    const data = await postJson(config.mkdirUrl, { path: state.currentPath, name });
    if (data.ok) {
      closeModal(elements.modalCreate);
      elements.createInput.value = "";
      loadDirectory(state.currentPath);
      setStatus(data.message || "Папка создана.", "success");
    } else {
      setStatus(data.error || "Не удалось создать папку.", "error");
    }
  }

  async function renameItem() {
    const paths = getSelectedPaths();
    if (paths.length !== 1) {
      setStatus("Выберите один файл или папку для переименования.", "warning");
      return;
    }
    const newName = elements.renameInput.value.trim();
    if (!newName) {
      setStatus("Введите новое имя.", "warning");
      return;
    }
    const data = await postJson(config.renameUrl, { path: paths[0], new_name: newName });
    if (data.ok) {
      closeModal(elements.modalRename);
      elements.renameInput.value = "";
      loadDirectory(state.currentPath);
      setStatus(data.message || "Переименование выполнено.", "success");
    } else {
      setStatus(data.error || "Не удалось переименовать.", "error");
    }
  }

  function startTask(taskId, label, onDone) {
    if (!taskId) return;
    const item = document.createElement("div");
    item.className = "fm-progress-item";
    const title = document.createElement("div");
    title.className = "fm-progress-title";
    title.textContent = label;
    const progress = document.createElement("progress");
    progress.max = 100;
    progress.value = 0;
    const message = document.createElement("div");
    message.className = "fm-progress-message";
    item.appendChild(title);
    item.appendChild(progress);
    item.appendChild(message);
    elements.progressList.prepend(item);

    async function poll() {
      try {
        const response = await fetch(config.taskUrl.replace("__TASK__", taskId));
        const data = await response.json();
        if (!data.ok) {
          message.textContent = data.error || "Задача не найдена.";
          progress.value = 100;
          return;
        }
        const task = data.task;
        progress.value = task.progress || 0;
        message.textContent = task.message || "";
        if (task.status === "running") {
          setTimeout(poll, 1000);
          return;
        }
        if (task.status === "error") {
          message.textContent = task.error || task.message || "Ошибка выполнения.";
          item.classList.add("fm-progress-error");
        } else {
          item.classList.add("fm-progress-done");
        }
        if (data.download_url) {
          const downloadBtn = document.createElement("button");
          downloadBtn.type = "button";
          downloadBtn.className = "btn";
          downloadBtn.textContent = "Скачать архив";
          downloadBtn.addEventListener("click", () => {
            window.open(data.download_url, "_blank");
          });
          item.appendChild(downloadBtn);
        }
        if (onDone) {
          onDone(task, data.download_url || "");
        }
      } catch (err) {
        message.textContent = `Ошибка: ${err}`;
        item.classList.add("fm-progress-error");
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
    if (!ok) {
      return;
    }
    const data = await postJson(config.deleteUrl, { paths });
    if (!data.ok) {
      setStatus(data.error || "Не удалось удалить.", "error");
      return;
    }
    startTask(data.task_id, "Удаление файлов", () => loadDirectory(state.currentPath));
  }

  async function copyOrMove(mode) {
    const paths = getSelectedPaths();
    if (!paths.length) {
      setStatus("Выберите файлы или папки.", "warning");
      return;
    }
    state.clipboard = { mode, items: paths.slice() };
    updateClipboard();
  }

  async function pasteClipboard() {
    if (!state.clipboard || !state.clipboard.items.length) {
      setStatus("Буфер пуст.", "warning");
      return;
    }
    if (state.clipboard.mode === "move") {
      const ok = await confirmAction("Переместить выбранные элементы?", { danger: true });
      if (!ok) {
        return;
      }
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
      () => loadDirectory(state.currentPath)
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
    startTask(data.task_id, "Подготовка архива", () => loadDirectory(state.currentPath));
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
      const data = await response.json();
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

  async function uploadFiles(files) {
    if (!files || !files.length) return;
    setStatus("Загрузка файлов...", "");
    const form = new FormData();
    Array.from(files).forEach((file) => form.append("files", file));
    try {
      const headers = {};
      if (csrfToken) {
        headers["X-CSRFToken"] = csrfToken;
      }
      const response = await fetch(`${config.uploadUrl}?path=${encodeURIComponent(state.currentPath)}`, {
        method: "POST",
        headers,
        body: form,
      });
      const data = await response.json();
      if (!data.ok) {
        setStatus(data.error || "Ошибка загрузки.", "error");
        return;
      }
      setStatus(data.message || "Файлы загружены.", "success");
      loadDirectory(state.currentPath);
    } catch (err) {
      setStatus(`Ошибка: ${err}`, "error");
    }
  }

  document.getElementById("fm-refresh").addEventListener("click", () => loadDirectory(state.currentPath));
  document.getElementById("fm-root").addEventListener("click", () => loadDirectory(""));
  document.getElementById("fm-up").addEventListener("click", () => {
    const path = state.currentPath;
    if (!path) {
      loadDirectory("");
      return;
    }
    const parts = path.replace(/\\\\$/, "").split(/[\\/]/);
    if (parts.length <= 1) {
      loadDirectory("");
      return;
    }
    parts.pop();
    const newPath = parts.join(path.includes("\\") ? "\\" : "/");
    loadDirectory(newPath);
  });
  document.getElementById("fm-go").addEventListener("click", () => {
    loadDirectory(elements.pathInput.value.trim());
  });
  elements.pathInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      loadDirectory(elements.pathInput.value.trim());
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
    const name = paths[0].split(/[\\/]/).pop();
    elements.renameInput.value = name || "";
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
    elements.tableBody.querySelectorAll("input[type='checkbox']").forEach((input) => {
      input.checked = checked;
      if (checked) {
        const row = input.closest("tr");
        if (row && row.dataset.path) {
          state.selected.add(row.dataset.path);
        }
      }
    });
    updateSelectionBadge();
  });

  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    [elements.modalCreate, elements.modalRename, elements.modalProps].forEach((modal) => {
      if (modal && !modal.hidden) {
        closeModal(modal);
      }
    });
  });

  loadDirectory("");
})();
