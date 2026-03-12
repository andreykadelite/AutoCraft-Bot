(() => {
  const byId = (id) => document.getElementById(id);

  const MOUSE_TYPES = new Set(["mouse_click", "mouse_double", "mouse_down", "mouse_up", "scroll"]);
  const TAB_ORDER = ["keyboard", "mouse", "presets", "history", "help"];
  const KEYBOARD_CATEGORY_ORDER = ["Windows", "Система", "Текст", "Браузер", "Проводник", "Скриншоты", "Клавиши по отдельности"];

  const normalizeToken = (value) => String(value || "").trim().toLowerCase();
  const toInt = (value, fallback = 0) => {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? Math.round(parsed) : fallback;
  };

  const safeDateTime = (unixTs) => {
    const value = Number(unixTs || 0);
    if (!value) return "";
    try {
      return new Date(value * 1000).toLocaleString();
    } catch (_err) {
      return "";
    }
  };

  const parseHotkey = (value) =>
    String(value || "")
      .split("+")
      .map((part) => normalizeToken(part))
      .filter(Boolean);

  const buildDefaultSingleKeys = () => {
    const options = [];
    const seen = new Set();
    const add = (value, label = "") => {
      const token = normalizeToken(value);
      if (!token || seen.has(token)) return;
      seen.add(token);
      options.push({ value: token, label: String(label || token).trim() || token });
    };

    [
      ["enter", "Enter"],
      ["tab", "Tab"],
      ["space", "Space"],
      ["backspace", "Backspace"],
      ["esc", "Esc"],
      ["delete", "Delete"],
      ["insert", "Insert"],
      ["home", "Home"],
      ["end", "End"],
      ["pageup", "Page Up"],
      ["pagedown", "Page Down"],
      ["up", "Arrow Up"],
      ["down", "Arrow Down"],
      ["left", "Arrow Left"],
      ["right", "Arrow Right"],
      ["printscreen", "Print Screen"],
      ["pause", "Pause"],
      ["capslock", "Caps Lock"],
      ["numlock", "Num Lock"],
      ["scrolllock", "Scroll Lock"],
      ["apps", "Context Menu"],
      ["winleft", "Win Left"],
      ["winright", "Win Right"],
      ["shift", "Shift"],
      ["ctrl", "Ctrl"],
      ["alt", "Alt"],
      ["`", "`"],
      ["-", "-"],
      ["=", "="],
      ["+", "+"],
      ["[", "["],
      ["]", "]"],
      ["\\", "\\"],
      [";", ";"],
      ["'", "'"],
      [",", ","],
      [".", "."],
      ["/", "/"],
    ].forEach(([value, label]) => add(value, label));

    for (let code = 97; code <= 122; code += 1) {
      const ch = String.fromCharCode(code);
      add(ch, ch.toUpperCase());
    }
    for (let digit = 0; digit <= 9; digit += 1) {
      add(String(digit), String(digit));
    }
    for (let idx = 1; idx <= 24; idx += 1) {
      add(`f${idx}`, `F${idx}`);
    }

    return options;
  };

  const actionSummary = (action) => {
    if (!action || typeof action !== "object") return "";
    const type = normalizeToken(action.type);
    if (type === "hotkey") {
      const keys = Array.isArray(action.keys) ? action.keys : [];
      return keys.join(" + ");
    }
    if (type === "key") return String(action.key || "");
    if (type === "text") {
      const text = String(action.text || "").replace(/\s+/g, " ").trim();
      return text.length > 60 ? `${text.slice(0, 57)}...` : text;
    }
    if (type === "scroll") {
      return `Прокрутка v=${toInt(action.vertical, 0)}, h=${toInt(action.horizontal, 0)}`;
    }
    if (MOUSE_TYPES.has(type)) {
      const btn = String(action.button || "left");
      return `${type} (${btn})`;
    }
    return type;
  };

  const cloneAction = (action) => {
    try {
      return JSON.parse(JSON.stringify(action || {}));
    } catch (_err) {
      return {};
    }
  };

  const isMouseAction = (action) => MOUSE_TYPES.has(normalizeToken(action?.type));
  const isTextAction = (action) => normalizeToken(action?.type) === "text";

  const createElem = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (typeof text === "string") node.textContent = text;
    return node;
  };

  const init = async () => {
    const root = byId("winkeys-root");
    if (!root) return;

    const cfg = {
      configUrl: root.dataset.configUrl || "",
      sendUrl: root.dataset.sendUrl || "",
      customSaveUrl: root.dataset.customSaveUrl || "",
      customDeleteUrl: root.dataset.customDeleteUrl || "",
      customClearUrl: root.dataset.customClearUrl || "",
      historyClearUrl: root.dataset.historyClearUrl || "",
      csrfToken: root.dataset.csrfToken || "",
    };

    const ui = {
      output: byId("winkeys-output"),
      storageFile: byId("winkeys-storage-file"),
      tablist: byId("winkeys-tabs"),
      tabs: {
        keyboard: byId("winkeys-tab-keyboard"),
        mouse: byId("winkeys-tab-mouse"),
        presets: byId("winkeys-tab-presets"),
        history: byId("winkeys-tab-history"),
        help: byId("winkeys-tab-help"),
      },
      panels: {
        keyboard: byId("winkeys-panel-keyboard"),
        mouse: byId("winkeys-panel-mouse"),
        presets: byId("winkeys-panel-presets"),
        history: byId("winkeys-panel-history"),
        help: byId("winkeys-panel-help"),
      },
      keyboard: {
        count: byId("winkeys-keyboard-count"),
        search: byId("winkeys-keyboard-search"),
        category: byId("winkeys-keyboard-category"),
        list: byId("winkeys-keyboard-builtins"),
        editId: byId("winkeys-keyboard-edit-id"),
        name: byId("winkeys-keyboard-name"),
        type: byId("winkeys-keyboard-type"),
        hotkeyRow: byId("winkeys-keyboard-hotkey-row"),
        keyRow: byId("winkeys-keyboard-key-row"),
        hotkey: byId("winkeys-keyboard-hotkey"),
        key: byId("winkeys-keyboard-key"),
        keyOptions: byId("winkeys-keyboard-key-options"),
        repeat: byId("winkeys-keyboard-repeat"),
        interval: byId("winkeys-keyboard-interval"),
        send: byId("winkeys-keyboard-send"),
        save: byId("winkeys-keyboard-save"),
        reset: byId("winkeys-keyboard-reset"),
      },
      mouse: {
        count: byId("winkeys-mouse-count"),
        search: byId("winkeys-mouse-search"),
        list: byId("winkeys-mouse-builtins"),
        editId: byId("winkeys-mouse-edit-id"),
        name: byId("winkeys-mouse-name"),
        type: byId("winkeys-mouse-type"),
        buttonRow: byId("winkeys-mouse-button-row"),
        scrollRow: byId("winkeys-mouse-scroll-row"),
        button: byId("winkeys-mouse-button"),
        vertical: byId("winkeys-mouse-vertical"),
        horizontal: byId("winkeys-mouse-horizontal"),
        repeat: byId("winkeys-mouse-repeat"),
        interval: byId("winkeys-mouse-interval"),
        send: byId("winkeys-mouse-send"),
        save: byId("winkeys-mouse-save"),
        reset: byId("winkeys-mouse-reset"),
      },
      custom: {
        list: byId("winkeys-custom-list"),
        clear: byId("winkeys-custom-clear"),
      },
      presetsText: {
        editId: byId("winkeys-preset-text-id"),
        name: byId("winkeys-preset-text-name"),
        repeat: byId("winkeys-preset-text-repeat"),
        interval: byId("winkeys-preset-text-interval"),
        text: byId("winkeys-preset-text-value"),
        send: byId("winkeys-preset-text-send"),
        save: byId("winkeys-preset-text-save"),
        reset: byId("winkeys-preset-text-reset"),
      },
      history: {
        list: byId("winkeys-history-list"),
        clear: byId("winkeys-history-clear"),
      },
      busyControls: [...root.querySelectorAll("[data-busy-lock]")],
    };

    const state = {
      activeTab: "keyboard",
      busy: false,
      builtins: [],
      keyboardBuiltins: [],
      mouseBuiltins: [],
      customActions: [],
      history: [],
      singleKeys: buildDefaultSingleKeys(),
      limits: {},
    };

    const messages = {
      loading: "Загрузка конфигурации...",
      ready: "Панель готова к работе.",
      csrfMissing: "CSRF-токен не найден. Обновите страницу.",
      sendRunning: "Отправка команды...",
      sendError: "Не удалось отправить команду.",
      configError: "Не удалось загрузить конфигурацию плагина.",
      keyboardActionEmpty: "Заполните поля клавиатурной команды.",
      mouseActionEmpty: "Заполните поля команды мыши.",
      presetTextEmpty: "Введите текст в блоке \"Текстовый пресет\".",
      customEmpty: "Пока нет сохранённых пресетов.",
      historyEmpty: "История пока пуста.",
      listEmpty: "По текущим фильтрам ничего не найдено.",
    };

    const setOutput = (message, ok) => {
      if (!ui.output) return;
      ui.output.textContent = message;
      ui.output.classList.toggle("is-ok", ok === true);
      ui.output.classList.toggle("is-error", ok === false);
      ui.output.setAttribute("aria-live", ok === false ? "assertive" : "polite");
    };

    const setBusy = (busy) => {
      state.busy = Boolean(busy);
      ui.busyControls.forEach((node) => {
        node.disabled = state.busy;
      });
    };

    const parseJsonResponse = async (response) => {
      try {
        return await response.json();
      } catch (_err) {
        return null;
      }
    };

    const postJson = async (url, payload) => {
      const response = await fetch(url, {
        method: "POST",
        credentials: "same-origin",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": cfg.csrfToken || "",
          Accept: "application/json",
        },
        body: JSON.stringify({
          ...(payload || {}),
          csrf_token: cfg.csrfToken || "",
        }),
      });
      const data = await parseJsonResponse(response);
      return { response, data };
    };

    const activateTab = (tabName, focusTab = false) => {
      if (!TAB_ORDER.includes(tabName)) return;
      state.activeTab = tabName;
      TAB_ORDER.forEach((name) => {
        const tab = ui.tabs[name];
        const panel = ui.panels[name];
        const selected = name === tabName;
        if (tab) {
          tab.setAttribute("aria-selected", selected ? "true" : "false");
          tab.tabIndex = selected ? 0 : -1;
          if (focusTab && selected) tab.focus();
        }
        if (panel) panel.hidden = !selected;
      });
    };

    const initTabs = () => {
      if (!ui.tablist) return;
      TAB_ORDER.forEach((name) => {
        const tab = ui.tabs[name];
        if (!tab) return;
        tab.addEventListener("click", () => activateTab(name));
        tab.addEventListener("keydown", (event) => {
          const currentIndex = TAB_ORDER.indexOf(name);
          if (currentIndex < 0) return;
          let nextIndex = currentIndex;
          if (event.key === "ArrowRight") nextIndex = (currentIndex + 1) % TAB_ORDER.length;
          else if (event.key === "ArrowLeft") nextIndex = (currentIndex - 1 + TAB_ORDER.length) % TAB_ORDER.length;
          else if (event.key === "Home") nextIndex = 0;
          else if (event.key === "End") nextIndex = TAB_ORDER.length - 1;
          else return;
          event.preventDefault();
          activateTab(TAB_ORDER[nextIndex], true);
        });
      });
    };

    const applyKeyboardTypeVisibility = () => {
      const type = normalizeToken(ui.keyboard.type?.value || "hotkey") === "key" ? "key" : "hotkey";
      if (ui.keyboard.type) ui.keyboard.type.value = type;
      if (ui.keyboard.hotkeyRow) ui.keyboard.hotkeyRow.hidden = type !== "hotkey";
      if (ui.keyboard.keyRow) ui.keyboard.keyRow.hidden = type !== "key";
    };

    const applyMouseTypeVisibility = () => {
      const type = normalizeToken(ui.mouse.type?.value || "mouse_click");
      const isScroll = type === "scroll";
      if (ui.mouse.buttonRow) ui.mouse.buttonRow.hidden = isScroll;
      if (ui.mouse.scrollRow) ui.mouse.scrollRow.hidden = !isScroll;
    };

    const resetKeyboardBuilder = () => {
      if (ui.keyboard.editId) ui.keyboard.editId.value = "";
      if (ui.keyboard.name) ui.keyboard.name.value = "";
      if (ui.keyboard.type) ui.keyboard.type.value = "hotkey";
      if (ui.keyboard.hotkey) ui.keyboard.hotkey.value = "";
      if (ui.keyboard.key) ui.keyboard.key.value = "";
      if (ui.keyboard.repeat) ui.keyboard.repeat.value = "1";
      if (ui.keyboard.interval) ui.keyboard.interval.value = "30";
      applyKeyboardTypeVisibility();
    };

    const resetMouseBuilder = () => {
      if (ui.mouse.editId) ui.mouse.editId.value = "";
      if (ui.mouse.name) ui.mouse.name.value = "";
      if (ui.mouse.type) ui.mouse.type.value = "mouse_click";
      if (ui.mouse.button) ui.mouse.button.value = "left";
      if (ui.mouse.vertical) ui.mouse.vertical.value = "400";
      if (ui.mouse.horizontal) ui.mouse.horizontal.value = "0";
      if (ui.mouse.repeat) ui.mouse.repeat.value = "1";
      if (ui.mouse.interval) ui.mouse.interval.value = "30";
      applyMouseTypeVisibility();
    };

    const resetPresetTextBuilder = () => {
      if (ui.presetsText.editId) ui.presetsText.editId.value = "";
      if (ui.presetsText.name) ui.presetsText.name.value = "";
      if (ui.presetsText.repeat) ui.presetsText.repeat.value = "1";
      if (ui.presetsText.interval) ui.presetsText.interval.value = "30";
      if (ui.presetsText.text) ui.presetsText.text.value = "";
    };

    const fillKeyboardBuilder = (item) => {
      const action = item?.action || {};
      if (isTextAction(action)) {
        fillPresetTextBuilder(item);
        return;
      }
      const type = normalizeToken(action.type) === "key" ? "key" : "hotkey";
      if (ui.keyboard.editId) ui.keyboard.editId.value = item?.id || "";
      if (ui.keyboard.name) ui.keyboard.name.value = item?.name || "";
      if (ui.keyboard.type) ui.keyboard.type.value = type;
      if (ui.keyboard.hotkey) ui.keyboard.hotkey.value = Array.isArray(action.keys) ? action.keys.join("+") : "";
      if (ui.keyboard.key) ui.keyboard.key.value = action.key || "";
      if (ui.keyboard.repeat) ui.keyboard.repeat.value = String(action.repeat || 1);
      if (ui.keyboard.interval) ui.keyboard.interval.value = String(action.interval_ms || 30);
      applyKeyboardTypeVisibility();
      activateTab("keyboard");
    };

    const fillMouseBuilder = (item) => {
      const action = item?.action || {};
      if (ui.mouse.editId) ui.mouse.editId.value = item?.id || "";
      if (ui.mouse.name) ui.mouse.name.value = item?.name || "";
      if (ui.mouse.type) ui.mouse.type.value = action.type || "mouse_click";
      if (ui.mouse.button) ui.mouse.button.value = action.button || "left";
      if (ui.mouse.vertical) ui.mouse.vertical.value = String(action.vertical || 0);
      if (ui.mouse.horizontal) ui.mouse.horizontal.value = String(action.horizontal || 0);
      if (ui.mouse.repeat) ui.mouse.repeat.value = String(action.repeat || 1);
      if (ui.mouse.interval) ui.mouse.interval.value = String(action.interval_ms || 30);
      applyMouseTypeVisibility();
      activateTab("mouse");
    };

    const fillPresetTextBuilder = (item) => {
      const action = item?.action || {};
      if (ui.presetsText.editId) ui.presetsText.editId.value = item?.id || "";
      if (ui.presetsText.name) ui.presetsText.name.value = item?.name || "";
      if (ui.presetsText.repeat) ui.presetsText.repeat.value = String(action.repeat || 1);
      if (ui.presetsText.interval) ui.presetsText.interval.value = String(action.interval_ms || 30);
      if (ui.presetsText.text) ui.presetsText.text.value = action.text || "";
      activateTab("presets");
    };

    const getKeyboardAction = () => {
      const type = normalizeToken(ui.keyboard.type?.value || "hotkey") === "key" ? "key" : "hotkey";
      const repeat = Math.max(1, Math.min(20, toInt(ui.keyboard.repeat?.value, 1)));
      const interval = Math.max(0, Math.min(2000, toInt(ui.keyboard.interval?.value, 30)));
      const action = { type, repeat, interval_ms: interval };
      if (type === "hotkey") {
        const keys = parseHotkey(ui.keyboard.hotkey?.value || "");
        if (!keys.length) return null;
        action.keys = keys;
        return action;
      }
      if (type === "key") {
        const key = normalizeToken(ui.keyboard.key?.value || "");
        if (!key) return null;
        action.key = key;
        return action;
      }
      return null;
    };

    const getMouseAction = () => {
      const type = normalizeToken(ui.mouse.type?.value || "mouse_click");
      const repeat = Math.max(1, Math.min(20, toInt(ui.mouse.repeat?.value, 1)));
      const interval = Math.max(0, Math.min(2000, toInt(ui.mouse.interval?.value, 30)));
      const action = { type, repeat, interval_ms: interval };
      if (type === "scroll") {
        const vertical = toInt(ui.mouse.vertical?.value, 0);
        const horizontal = toInt(ui.mouse.horizontal?.value, 0);
        if (!vertical && !horizontal) return null;
        action.vertical = vertical;
        action.horizontal = horizontal;
        return action;
      }
      if (!MOUSE_TYPES.has(type)) return null;
      action.button = normalizeToken(ui.mouse.button?.value || "left");
      return action;
    };

    const getPresetTextAction = () => {
      const text = String(ui.presetsText.text?.value || "");
      if (!text.trim()) return null;
      const repeat = Math.max(1, Math.min(20, toInt(ui.presetsText.repeat?.value, 1)));
      const interval = Math.max(0, Math.min(2000, toInt(ui.presetsText.interval?.value, 30)));
      return {
        type: "text",
        text,
        repeat,
        interval_ms: interval,
      };
    };

    const sendAction = async (action, meta) => {
      if (!action) return;
      if (!cfg.csrfToken) {
        setOutput(messages.csrfMissing, false);
        return;
      }
      if (state.busy) return;
      setBusy(true);
      setOutput(messages.sendRunning, true);
      try {
        const { response, data } = await postJson(cfg.sendUrl, {
          action,
          source: meta?.source || "manual",
          name: meta?.name || "",
        });
        if (!response.ok || !data || !data.ok) {
          const errorText = (data && (data.error || data.message)) || messages.sendError;
          setOutput(errorText.startsWith("Ошибка") ? errorText : `Ошибка: ${errorText}`, false);
          return;
        }
        state.history = Array.isArray(data.history) ? data.history : state.history;
        renderHistoryList();
        setOutput(data.message || "OK: команда отправлена.", true);
      } catch (_err) {
        setOutput(messages.sendError, false);
      } finally {
        setBusy(false);
      }
    };

    const saveCustomAction = async (payload) => {
      if (!cfg.csrfToken) {
        setOutput(messages.csrfMissing, false);
        return false;
      }
      if (state.busy) return false;
      setBusy(true);
      try {
        const { response, data } = await postJson(cfg.customSaveUrl, payload);
        if (!response.ok || !data || !data.ok) {
          setOutput((data && (data.error || data.message)) || messages.sendError, false);
          return false;
        }
        state.customActions = Array.isArray(data.custom_actions) ? data.custom_actions : [];
        renderCustomList();
        setOutput(data.message || "Пресет сохранён.", true);
        return true;
      } catch (_err) {
        setOutput(messages.sendError, false);
        return false;
      } finally {
        setBusy(false);
      }
    };

    const getKeyboardCategoryStats = () => {
      const map = new Map();
      state.keyboardBuiltins.forEach((item) => {
        const category = String(item?.category || "").trim();
        if (!category) return;
        map.set(category, (map.get(category) || 0) + 1);
      });

      const orderIndex = (category) => {
        const index = KEYBOARD_CATEGORY_ORDER.indexOf(category);
        return index >= 0 ? index : Number.MAX_SAFE_INTEGER;
      };

      const stats = [...map.entries()].map(([name, count]) => ({ name, count }));
      stats.sort((a, b) => {
        const indexDiff = orderIndex(a.name) - orderIndex(b.name);
        if (indexDiff !== 0) return indexDiff;
        return a.name.localeCompare(b.name, "ru");
      });
      return stats;
    };

    const populateKeyboardCategoryFilter = () => {
      if (!ui.keyboard.category) return;
      const selected = ui.keyboard.category.value || "";
      const stats = getKeyboardCategoryStats();
      const categories = stats.map((entry) => entry.name);
      const total = state.keyboardBuiltins.length;
      ui.keyboard.category.innerHTML = "";
      const emptyOption = createElem("option", "", `Все категории (${total})`);
      emptyOption.value = "";
      ui.keyboard.category.appendChild(emptyOption);
      stats.forEach((entry) => {
        const option = createElem("option", "", `${entry.name} (${entry.count})`);
        option.value = entry.name;
        ui.keyboard.category.appendChild(option);
      });
      ui.keyboard.category.value = categories.includes(selected) ? selected : "";
    };

    const renderKeyboardKeyOptions = () => {
      if (!ui.keyboard.keyOptions) return;
      const source = Array.isArray(state.singleKeys) && state.singleKeys.length ? state.singleKeys : buildDefaultSingleKeys();
      const seen = new Set();
      ui.keyboard.keyOptions.innerHTML = "";

      source.forEach((item) => {
        const value = normalizeToken(typeof item === "string" ? item : item?.value);
        if (!value || seen.has(value)) return;
        seen.add(value);

        const labelRaw = typeof item === "string" ? "" : String(item?.label || "").trim();
        const option = document.createElement("option");
        option.value = value;
        if (labelRaw && normalizeToken(labelRaw) !== value) {
          option.label = `${labelRaw} (${value})`;
        }
        ui.keyboard.keyOptions.appendChild(option);
      });
    };

    const renderKeyboardBuiltins = () => {
      if (!ui.keyboard.list) return;
      const query = normalizeToken(ui.keyboard.search?.value || "");
      const category = ui.keyboard.category?.value || "";
      const total = state.keyboardBuiltins.length;
      const items = state.keyboardBuiltins.filter((item) => {
        if (category && item.category !== category) return false;
        if (!query) return true;
        const hay = `${item.name || ""} ${item.combo || ""} ${item.category || ""}`.toLowerCase();
        return hay.includes(query);
      });

      ui.keyboard.list.innerHTML = "";
      if (ui.keyboard.count) {
        ui.keyboard.count.textContent = `Показано: ${items.length} из ${total}`;
      }
      if (!items.length) {
        ui.keyboard.list.appendChild(createElem("div", "winkeys-empty", messages.listEmpty));
        return;
      }

      items.forEach((item) => {
        const card = createElem("article", "winkeys-item");
        card.setAttribute("role", "listitem");
        const blockedReason = String(item.blocked_reason || "").trim();

        const title = createElem("h4", "winkeys-item-title", item.name || "Команда");
        card.appendChild(title);
        card.appendChild(createElem("div", "winkeys-item-meta", `${item.category || ""} | ${item.combo || actionSummary(item.action)}`));
        if (blockedReason) {
          card.appendChild(createElem("div", "winkeys-item-note", blockedReason));
        }

        const actions = createElem("div", "winkeys-item-actions");
        const sendBtn = createElem("button", "btn-primary", "Отправить");
        sendBtn.type = "button";
        sendBtn.disabled = Boolean(blockedReason);
        if (blockedReason) sendBtn.title = blockedReason;
        sendBtn.addEventListener("click", () => {
          sendAction(cloneAction(item.action), { source: "builtin", name: item.name || "Команда" });
        });
        actions.appendChild(sendBtn);

        const editBtn = createElem("button", "btn", "В редактор");
        editBtn.type = "button";
        editBtn.addEventListener("click", () => {
          fillKeyboardBuilder({ id: "", name: item.name || "", action: cloneAction(item.action) });
        });
        actions.appendChild(editBtn);

        card.appendChild(actions);
        ui.keyboard.list.appendChild(card);
      });
    };

    const renderMouseBuiltins = () => {
      if (!ui.mouse.list) return;
      const query = normalizeToken(ui.mouse.search?.value || "");
      const items = state.mouseBuiltins.filter((item) => {
        if (!query) return true;
        const hay = `${item.name || ""} ${item.combo || ""}`.toLowerCase();
        return hay.includes(query);
      });
      ui.mouse.list.innerHTML = "";
      if (ui.mouse.count) ui.mouse.count.textContent = `${items.length} шт.`;
      if (!items.length) {
        ui.mouse.list.appendChild(createElem("div", "winkeys-empty", messages.listEmpty));
        return;
      }

      items.forEach((item) => {
        const card = createElem("article", "winkeys-item");
        card.setAttribute("role", "listitem");
        const blockedReason = String(item.blocked_reason || "").trim();
        card.appendChild(createElem("h4", "winkeys-item-title", item.name || "Действие"));
        card.appendChild(createElem("div", "winkeys-item-meta", item.combo || actionSummary(item.action)));
        if (blockedReason) {
          card.appendChild(createElem("div", "winkeys-item-note", blockedReason));
        }

        const actions = createElem("div", "winkeys-item-actions");
        const sendBtn = createElem("button", "btn-primary", "Отправить");
        sendBtn.type = "button";
        sendBtn.disabled = Boolean(blockedReason);
        if (blockedReason) sendBtn.title = blockedReason;
        sendBtn.addEventListener("click", () => {
          sendAction(cloneAction(item.action), { source: "builtin", name: item.name || "Действие" });
        });
        actions.appendChild(sendBtn);

        const editBtn = createElem("button", "btn", "В редактор");
        editBtn.type = "button";
        editBtn.addEventListener("click", () => {
          fillMouseBuilder({ id: "", name: item.name || "", action: cloneAction(item.action) });
        });
        actions.appendChild(editBtn);

        card.appendChild(actions);
        ui.mouse.list.appendChild(card);
      });
    };

    const renderCustomList = () => {
      if (!ui.custom.list) return;
      const items = Array.isArray(state.customActions) ? state.customActions : [];
      ui.custom.list.innerHTML = "";
      if (!items.length) {
        ui.custom.list.appendChild(createElem("div", "winkeys-empty", messages.customEmpty));
        return;
      }

      items.forEach((item) => {
        const row = createElem("article", "winkeys-row");
        row.setAttribute("role", "listitem");

        const textBox = createElem("div", "winkeys-row-text");
        textBox.appendChild(createElem("strong", "", item.name || "Пресет"));
        const updated = safeDateTime(item.updated_at);
        textBox.appendChild(createElem("small", "", `${actionSummary(item.action)}${updated ? ` | ${updated}` : ""}`));
        row.appendChild(textBox);

        const actions = createElem("div", "winkeys-row-actions");
        const runBtn = createElem("button", "btn-primary", "Запуск");
        runBtn.type = "button";
        runBtn.addEventListener("click", () => {
          sendAction(cloneAction(item.action), { source: "custom", name: item.name || "Пресет" });
        });
        actions.appendChild(runBtn);

        const editBtn = createElem("button", "btn", "Редактировать");
        editBtn.type = "button";
        editBtn.addEventListener("click", () => {
          if (isMouseAction(item.action)) fillMouseBuilder(item);
          else if (isTextAction(item.action)) fillPresetTextBuilder(item);
          else fillKeyboardBuilder(item);
        });
        actions.appendChild(editBtn);

        const deleteBtn = createElem("button", "btn btn-danger", "Удалить");
        deleteBtn.type = "button";
        deleteBtn.addEventListener("click", async () => {
          if (!cfg.csrfToken || state.busy) return;
          setBusy(true);
          try {
            const { response, data } = await postJson(cfg.customDeleteUrl, { id: item.id });
            if (!response.ok || !data || !data.ok) {
              setOutput((data && (data.error || data.message)) || messages.sendError, false);
              return;
            }
            state.customActions = Array.isArray(data.custom_actions) ? data.custom_actions : [];
            renderCustomList();
            setOutput(data.message || "Пресет удалён.", true);
          } catch (_err) {
            setOutput(messages.sendError, false);
          } finally {
            setBusy(false);
          }
        });
        actions.appendChild(deleteBtn);

        row.appendChild(actions);
        ui.custom.list.appendChild(row);
      });
    };

    const renderHistoryList = () => {
      if (!ui.history.list) return;
      const items = Array.isArray(state.history) ? state.history : [];
      ui.history.list.innerHTML = "";
      if (!items.length) {
        ui.history.list.appendChild(createElem("div", "winkeys-empty", messages.historyEmpty));
        return;
      }

      items.forEach((item) => {
        const row = createElem("article", "winkeys-row");
        row.setAttribute("role", "listitem");
        const textBox = createElem("div", "winkeys-row-text");
        textBox.appendChild(createElem("strong", "", item.name || "Команда"));
        const status = item.ok ? "OK" : "Ошибка";
        const ts = safeDateTime(item.ts);
        textBox.appendChild(createElem("small", "", `${item.summary || actionSummary(item.action)} | ${status}${ts ? ` | ${ts}` : ""}`));
        if (item.message) {
          textBox.appendChild(createElem("small", "winkeys-item-note", String(item.message)));
        }
        row.appendChild(textBox);

        const actions = createElem("div", "winkeys-row-actions");
        const rerun = createElem("button", "btn", "Повтор");
        rerun.type = "button";
        rerun.addEventListener("click", () => {
          sendAction(cloneAction(item.action), { source: "history", name: item.name || "Команда" });
        });
        actions.appendChild(rerun);

        const open = createElem("button", "btn", "В редактор");
        open.type = "button";
        open.addEventListener("click", () => {
          const editable = { id: "", name: item.name || "", action: cloneAction(item.action) };
          if (isMouseAction(editable.action)) fillMouseBuilder(editable);
          else if (isTextAction(editable.action)) fillPresetTextBuilder(editable);
          else fillKeyboardBuilder(editable);
        });
        actions.appendChild(open);

        row.appendChild(actions);
        ui.history.list.appendChild(row);
      });
    };

    const refreshDerivedCollections = () => {
      state.keyboardBuiltins = state.builtins.filter((item) => !isMouseAction(item.action));
      state.mouseBuiltins = state.builtins.filter((item) => isMouseAction(item.action));
      populateKeyboardCategoryFilter();
      renderKeyboardBuiltins();
      renderMouseBuiltins();
      renderCustomList();
      renderHistoryList();
    };

    const loadConfig = async () => {
      if (!cfg.configUrl) {
        setOutput(messages.configError, false);
        return;
      }
      setOutput(messages.loading, true);
      try {
        const response = await fetch(cfg.configUrl, {
          method: "GET",
          credentials: "same-origin",
          headers: { Accept: "application/json" },
        });
        const data = await parseJsonResponse(response);
        if (!response.ok || !data || !data.ok) {
          setOutput((data && (data.error || data.message)) || messages.configError, false);
          return;
        }

        state.builtins = Array.isArray(data.builtins) ? data.builtins : [];
        state.customActions = Array.isArray(data.custom_actions) ? data.custom_actions : [];
        state.history = Array.isArray(data.history) ? data.history : [];
        state.singleKeys = Array.isArray(data.single_keys) && data.single_keys.length ? data.single_keys : buildDefaultSingleKeys();
        state.limits = data.limits || {};

        if (ui.storageFile) ui.storageFile.textContent = data.storage_file || "-";

        renderKeyboardKeyOptions();
        refreshDerivedCollections();

        if (Array.isArray(data.warnings) && data.warnings.length) {
          setOutput(data.warnings.join(" | "), false);
        } else {
          setOutput(messages.ready, true);
        }
      } catch (_err) {
        setOutput(messages.configError, false);
      }
    };

    ui.keyboard.type?.addEventListener("change", applyKeyboardTypeVisibility);
    ui.mouse.type?.addEventListener("change", applyMouseTypeVisibility);
    ui.keyboard.search?.addEventListener("input", renderKeyboardBuiltins);
    ui.keyboard.category?.addEventListener("change", renderKeyboardBuiltins);
    ui.mouse.search?.addEventListener("input", renderMouseBuiltins);

    ui.keyboard.send?.addEventListener("click", async () => {
      const action = getKeyboardAction();
      if (!action) {
        setOutput(messages.keyboardActionEmpty, false);
        return;
      }
      const name = String(ui.keyboard.name?.value || "").trim() || "Клавиатурная команда";
      await sendAction(action, { source: "manual", name });
    });

    ui.keyboard.save?.addEventListener("click", async () => {
      const action = getKeyboardAction();
      if (!action) {
        setOutput(messages.keyboardActionEmpty, false);
        return;
      }
      const ok = await saveCustomAction({
        id: ui.keyboard.editId?.value || "",
        name: String(ui.keyboard.name?.value || "").trim(),
        action,
      });
      if (ok) resetKeyboardBuilder();
    });

    ui.keyboard.reset?.addEventListener("click", () => resetKeyboardBuilder());

    ui.mouse.send?.addEventListener("click", async () => {
      const action = getMouseAction();
      if (!action) {
        setOutput(messages.mouseActionEmpty, false);
        return;
      }
      const name = String(ui.mouse.name?.value || "").trim() || "Команда мыши";
      await sendAction(action, { source: "manual", name });
    });

    ui.mouse.save?.addEventListener("click", async () => {
      const action = getMouseAction();
      if (!action) {
        setOutput(messages.mouseActionEmpty, false);
        return;
      }
      const ok = await saveCustomAction({
        id: ui.mouse.editId?.value || "",
        name: String(ui.mouse.name?.value || "").trim(),
        action,
      });
      if (ok) resetMouseBuilder();
    });

    ui.mouse.reset?.addEventListener("click", () => resetMouseBuilder());

    ui.presetsText.send?.addEventListener("click", async () => {
      const action = getPresetTextAction();
      if (!action) {
        setOutput(messages.presetTextEmpty, false);
        return;
      }
      const name = String(ui.presetsText.name?.value || "").trim() || "Текстовый пресет";
      await sendAction(action, { source: "manual", name });
    });

    ui.presetsText.save?.addEventListener("click", async () => {
      const action = getPresetTextAction();
      if (!action) {
        setOutput(messages.presetTextEmpty, false);
        return;
      }
      const ok = await saveCustomAction({
        id: ui.presetsText.editId?.value || "",
        name: String(ui.presetsText.name?.value || "").trim(),
        action,
      });
      if (ok) resetPresetTextBuilder();
    });

    ui.presetsText.reset?.addEventListener("click", () => resetPresetTextBuilder());

    ui.custom.clear?.addEventListener("click", async () => {
      if (!cfg.csrfToken || state.busy) return;
      setBusy(true);
      try {
        const { response, data } = await postJson(cfg.customClearUrl, {});
        if (!response.ok || !data || !data.ok) {
          setOutput((data && (data.error || data.message)) || messages.sendError, false);
          return;
        }
        state.customActions = [];
        renderCustomList();
        resetKeyboardBuilder();
        resetMouseBuilder();
        resetPresetTextBuilder();
        setOutput(data.message || "Список пресетов очищен.", true);
      } catch (_err) {
        setOutput(messages.sendError, false);
      } finally {
        setBusy(false);
      }
    });

    ui.history.clear?.addEventListener("click", async () => {
      if (!cfg.csrfToken || state.busy) return;
      setBusy(true);
      try {
        const { response, data } = await postJson(cfg.historyClearUrl, {});
        if (!response.ok || !data || !data.ok) {
          setOutput((data && (data.error || data.message)) || messages.sendError, false);
          return;
        }
        state.history = [];
        renderHistoryList();
        setOutput(data.message || "История очищена.", true);
      } catch (_err) {
        setOutput(messages.sendError, false);
      } finally {
        setBusy(false);
      }
    });

    root.addEventListener("keydown", (event) => {
      if (event.key !== "Enter" || !(event.ctrlKey || event.metaKey)) return;
      if (state.activeTab === "keyboard") {
        event.preventDefault();
        ui.keyboard.send?.click();
      } else if (state.activeTab === "mouse") {
        event.preventDefault();
        ui.mouse.send?.click();
      } else if (state.activeTab === "presets") {
        event.preventDefault();
        ui.presetsText.send?.click();
      }
    });

    initTabs();
    resetKeyboardBuilder();
    resetMouseBuilder();
    resetPresetTextBuilder();
    renderKeyboardKeyOptions();
    applyKeyboardTypeVisibility();
    applyMouseTypeVisibility();
    activateTab("keyboard");
    await loadConfig();
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();

