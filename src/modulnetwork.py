
from typing import Dict, List, Optional

from aiogram import types
from aiogram.dispatcher import Dispatcher

from __main__ import authorized_users, write_bot_log
from keymenu import get_utilities_keyboard
from network_module import diagnostics, firewall, keyboards as kb, netinfo, utils

# --- Состояние по пользователям ---
net_mode: Dict[int, bool] = {}
net_section: Dict[int, str] = {}
net_step: Dict[int, str] = {}
net_context: Dict[int, Dict[str, str]] = {}
port_cache: Dict[int, List[Dict[str, str]]] = {}
proc_cache: Dict[int, List[Dict[str, str]]] = {}
adapter_cache: Dict[int, List[str]] = {}
port_page: Dict[int, int] = {}
proc_page: Dict[int, int] = {}
adapter_page: Dict[int, int] = {}
intro_sent = False

POPULAR_PORTS = [21, 22, 23, 25, 53, 80, 110, 135, 139, 143, 389, 443, 445, 465, 587, 3306, 3389, 5432, 5631, 5900, 8080, 8443]
PAGE_SIZE = 10


# --- Вспомогательные функции ---
async def send_chunked(message: types.Message, text: str, reply_markup: Optional[types.ReplyKeyboardMarkup] = None) -> None:
    """Отправка длинного текста кусками, учитывая лимит Telegram."""
    parts = utils.chunk_text(text)
    last = len(parts) - 1
    for idx, part in enumerate(parts):
        await message.answer(
            f"```text\n{part}\n```",
            parse_mode="Markdown",
            reply_markup=reply_markup if idx == last else None,
        )


def reset_user(user_id: int) -> None:
    net_section.pop(user_id, None)
    net_step.pop(user_id, None)
    net_context.pop(user_id, None)
    port_cache.pop(user_id, None)
    proc_cache.pop(user_id, None)
    adapter_cache.pop(user_id, None)
    port_page.pop(user_id, None)
    proc_page.pop(user_id, None)
    adapter_page.pop(user_id, None)

def build_port_keyboard(user_id: int, action: str) -> types.ReplyKeyboardMarkup:
    kb_ports = types.ReplyKeyboardMarkup(resize_keyboard=True)
    ports = port_cache.get(user_id, [])
    total = len(ports)
    if total == 0:
        kb_ports.add("Обновить список портов")
        kb_ports.add("Назад в модуль сети")
        return kb_ports

    page = port_page.get(user_id, 0)
    total_pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
    page = max(0, min(page, total_pages - 1))
    port_page[user_id] = page

    start = page * PAGE_SIZE
    end = start + PAGE_SIZE
    for entry in ports[start:end]:
        kb_ports.add(f"{entry['port']}/{entry['proto']} | PID {entry['pid']} | {entry['name']}")

    nav = []
    if page > 0:
        nav.append("Предыдущая страница портов")
    if page < total_pages - 1:
        nav.append("Следующая страница портов")
    if nav:
        kb_ports.row(*nav)

    kb_ports.add("Обновить список портов")
    kb_ports.add("Назад в модуль сети")
    net_step[user_id] = action
    return kb_ports


def build_process_keyboard(user_id: int, action: str) -> types.ReplyKeyboardMarkup:
    kb_proc = types.ReplyKeyboardMarkup(resize_keyboard=True)
    procs = proc_cache.get(user_id, [])
    total = len(procs)
    if total == 0:
        kb_proc.add("Обновить список процессов")
        kb_proc.add("Назад в модуль сети")
        return kb_proc

    page = proc_page.get(user_id, 0)
    total_pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
    page = max(0, min(page, total_pages - 1))
    proc_page[user_id] = page

    start = page * PAGE_SIZE
    end = start + PAGE_SIZE
    for proc in procs[start:end]:
        kb_proc.add(f"{proc['pid']} | {proc['name']}")

    nav = []
    if page > 0:
        nav.append("Предыдущая страница процессов")
    if page < total_pages - 1:
        nav.append("Следующая страница процессов")
    if nav:
        kb_proc.row(*nav)

    kb_proc.add("Обновить список процессов")
    kb_proc.add("Назад в модуль сети")
    net_step[user_id] = action
    return kb_proc


def build_adapter_keyboard(user_id: int, action: str) -> types.ReplyKeyboardMarkup:
    kb_adapt = types.ReplyKeyboardMarkup(resize_keyboard=True)
    adapters = adapter_cache.get(user_id, [])
    total = len(adapters)
    if total == 0:
        kb_adapt.add("Обновить список адаптеров")
        kb_adapt.add("Назад в модуль сети")
        return kb_adapt

    page = adapter_page.get(user_id, 0)
    total_pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
    page = max(0, min(page, total_pages - 1))
    adapter_page[user_id] = page

    start = page * PAGE_SIZE
    end = start + PAGE_SIZE
    for name in adapters[start:end]:
        kb_adapt.add(name)

    nav = []
    if page > 0:
        nav.append("Предыдущая страница адаптеров")
    if page < total_pages - 1:
        nav.append("Следующая страница адаптеров")
    if nav:
        kb_adapt.row(*nav)

    kb_adapt.add("Обновить список адаптеров")
    kb_adapt.add("Назад в модуль сети")
    net_step[user_id] = action
    return kb_adapt


def refresh_ports(user_id: int) -> None:
    port_cache[user_id] = netinfo.listening_entries(limit=0)
    port_page[user_id] = 0


def refresh_processes(user_id: int) -> None:
    proc_cache[user_id] = netinfo.running_processes(limit=80)
    proc_page[user_id] = 0


def refresh_adapters(user_id: int) -> None:
    adapter_cache[user_id] = netinfo.adapter_names()
    adapter_page[user_id] = 0

def parse_port_from_button(text: str) -> Optional[Dict[str, str]]:
    parts = text.split("|", 1)
    if not parts:
        return None
    left = parts[0].strip()
    if "/" not in left:
        return None
    port_part, proto_part = left.split("/", 1)
    try:
        port = int(port_part.strip())
    except ValueError:
        return None
    proto = (proto_part or "").strip().upper() or "TCP"
    return {"port": port, "proto": proto}


def parse_proc_from_button(user_id: int, text: str) -> Optional[Dict[str, str]]:
    try:
        pid = int(text.split("|", 1)[0].strip())
    except Exception:
        return None
    for proc in proc_cache.get(user_id, []):
        if proc.get("pid") == pid:
            return proc
    return None


def current_keyboard(user_id: int) -> types.ReplyKeyboardMarkup:
    section = net_section.get(user_id)
    if section == "monitor":
        return kb.get_monitor_keyboard()
    if section == "connections":
        return kb.get_connections_keyboard()
    if section == "ports":
        return kb.get_ports_keyboard()
    if section == "firewall":
        return kb.get_firewall_keyboard()
    if section == "adapters":
        return kb.get_adapters_keyboard()
    if section == "diag":
        return kb.get_diag_keyboard()
    if section == "scan":
        return kb.get_scan_keyboard()
    return kb.get_network_main_keyboard()

def register_handlers(dp: Dispatcher):
    """Регистрация хендлеров для модуля работы с сетью."""

    # --- Вход/выход ---
    @dp.message_handler(lambda m: m.from_user.id in authorized_users and m.text == "Работа с сетью")
    async def net_entry(message: types.Message):
        user_id = message.from_user.id
        net_mode[user_id] = True
        reset_user(user_id)
        write_bot_log(f"Пользователь {user_id} открыл модуль сети.")
        global intro_sent
        if not intro_sent:
            intro_sent = True
            await message.answer(
                "🛰️ Модуль «Работа с сетью» загружен.\n"
                "Функции: мониторинг трафика и соединений, управление портами/процессами, правила брандмауэра, "
                "работа с адаптерами, диагностика (ping/traceroute, DNS, IP), сканер популярных портов, карточка сети.\n"
                "Все действия выполняются через кнопки."
            )
        await message.answer(
            "🛰️ Модуль работы с сетью. Выбирай раздел: мониторинг, соединения, порты/процессы, брандмауэр, адаптеры, диагностика или сканер.",
            reply_markup=kb.get_network_main_keyboard(),
        )

    @dp.message_handler(lambda m: m.from_user.id in authorized_users and net_mode.get(m.from_user.id) and m.text == "Назад в утилиты")
    async def net_back_to_utils(message: types.Message):
        user_id = message.from_user.id
        net_mode[user_id] = False
        reset_user(user_id)
        await message.answer("Возврат в раздел утилит.", reply_markup=get_utilities_keyboard())

    @dp.message_handler(lambda m: m.from_user.id in authorized_users and net_mode.get(m.from_user.id) and m.text == "Назад в модуль сети")
    async def net_back_to_main(message: types.Message):
        user_id = message.from_user.id
        net_step.pop(user_id, None)
        net_context.pop(user_id, None)
        await message.answer("Главное меню сети.", reply_markup=kb.get_network_main_keyboard())

    # --- Мониторинг и инфо ---
    @dp.message_handler(lambda m: m.from_user.id in authorized_users and net_mode.get(m.from_user.id) and m.text in {"Мониторинг трафика", "Обновить трафик"})
    async def net_monitor(message: types.Message):
        user_id = message.from_user.id
        net_section[user_id] = "monitor"
        net_step.pop(user_id, None)
        report = netinfo.traffic_report()
        await send_chunked(message, report, reply_markup=kb.get_monitor_keyboard())

    @dp.message_handler(lambda m: m.from_user.id in authorized_users and net_mode.get(m.from_user.id) and m.text == "Информация о сети")
    async def net_info_network(message: types.Message):
        user_id = message.from_user.id
        net_section[user_id] = "info"
        net_step.pop(user_id, None)
        report = netinfo.network_overview()
        await send_chunked(message, report, reply_markup=kb.get_network_main_keyboard())

    @dp.message_handler(lambda m: m.from_user.id in authorized_users and net_mode.get(m.from_user.id) and m.text == "Информация о модуле")
    async def net_module_info(message: types.Message):
        text = (
            "Модуль «Работа с сетью»:\n"
            "- Мониторинг: трафик, активные соединения.\n"
            "- Порты/процессы: просмотр, завершение по порту, (раз)блокировка порта.\n"
            "- Брандмауэр: блокировка приложений, список правил бота, блок/разблок портов.\n"
            "- Адаптеры: статус, включение/отключение.\n"
            "- Диагностика: ping, traceroute, flushdns, renew/release, ipconfig.\n"
            "- Сканер: быстрые проверки популярных портов.\n"
            "Все действия выполняются через кнопки."
        )
        await message.answer(text, reply_markup=kb.get_network_main_keyboard())
    # --- Соединения ---
    @dp.message_handler(lambda m: m.from_user.id in authorized_users and net_mode.get(m.from_user.id) and m.text in {"Активные соединения", "Обновить соединения"})
    async def net_connections(message: types.Message):
        user_id = message.from_user.id
        net_section[user_id] = "connections"
        net_step.pop(user_id, None)
        report = netinfo.connections_overview()
        await send_chunked(message, report, reply_markup=kb.get_connections_keyboard())

    # --- Порты и процессы ---
    @dp.message_handler(lambda m: m.from_user.id in authorized_users and net_mode.get(m.from_user.id) and m.text == "Порты и процессы")
    async def net_ports_menu(message: types.Message):
        user_id = message.from_user.id
        net_section[user_id] = "ports"
        refresh_ports(user_id)
        net_step.pop(user_id, None)
        await message.answer(
            "Раздел портов и процессов. Выбери действие: информация, завершение, блокировка/разблокировка. Обновляй список для актуальных данных.",
            reply_markup=kb.get_ports_keyboard(),
        )

    @dp.message_handler(lambda m: m.from_user.id in authorized_users and net_section.get(m.from_user.id) == "ports" and m.text == "Информация о портах")
    async def net_ports_full_info(message: types.Message):
        report = netinfo.full_ports_report()
        await send_chunked(message, report, reply_markup=kb.get_ports_keyboard())

    @dp.message_handler(lambda m: m.from_user.id in authorized_users and net_section.get(m.from_user.id) == "ports" and m.text == "Обновить список портов")
    async def net_ports_refresh(message: types.Message):
        user_id = message.from_user.id
        refresh_ports(user_id)
        await message.answer("Список портов обновлён. Выбери нужный порт.", reply_markup=kb.get_ports_keyboard())

    @dp.message_handler(lambda m: m.from_user.id in authorized_users and net_section.get(m.from_user.id) == "ports" and m.text == "Информация по порту")
    async def net_port_info(message: types.Message):
        user_id = message.from_user.id
        kb_ports = build_port_keyboard(user_id, "port_info_select")
        await message.answer("Выбери порт для просмотра информации:", reply_markup=kb_ports)

    @dp.message_handler(lambda m: m.from_user.id in authorized_users and net_section.get(m.from_user.id) == "ports" and m.text == "Завершить процесс на порту")
    async def net_port_kill(message: types.Message):
        user_id = message.from_user.id
        kb_ports = build_port_keyboard(user_id, "port_kill_select")
        await message.answer("Выбери порт: процесс будет завершён после подтверждения.", reply_markup=kb_ports)

    @dp.message_handler(lambda m: m.from_user.id in authorized_users and net_section.get(m.from_user.id) == "ports" and m.text == "Блокировать порт")
    async def net_fw_block_port(message: types.Message):
        user_id = message.from_user.id
        kb_ports = build_port_keyboard(user_id, "fw_block_select")
        await message.answer("Выбери порт для блокировки в брандмауэре:", reply_markup=kb_ports)

    @dp.message_handler(lambda m: m.from_user.id in authorized_users and net_section.get(m.from_user.id) == "ports" and m.text == "Разблокировать порт")
    async def net_fw_unblock_port(message: types.Message):
        user_id = message.from_user.id
        kb_ports = build_port_keyboard(user_id, "fw_unblock_select")
        await message.answer("Выбери порт для разблокировки:", reply_markup=kb_ports)
    # --- Брандмауэр (приложения) ---
    @dp.message_handler(lambda m: m.from_user.id in authorized_users and net_mode.get(m.from_user.id) and m.text == "Брандмауэр")
    async def net_firewall_menu(message: types.Message):
        user_id = message.from_user.id
        net_section[user_id] = "firewall"
        net_step.pop(user_id, None)
        await message.answer("Управление правилами брандмауэра.", reply_markup=kb.get_firewall_keyboard())

    @dp.message_handler(lambda m: m.from_user.id in authorized_users and net_section.get(m.from_user.id) == "firewall" and m.text == "Обновить список процессов")
    async def fw_refresh_procs(message: types.Message):
        user_id = message.from_user.id
        refresh_processes(user_id)
        await message.answer("Список процессов обновлён.", reply_markup=kb.get_firewall_keyboard())

    @dp.message_handler(lambda m: m.from_user.id in authorized_users and net_section.get(m.from_user.id) == "firewall" and m.text == "Блокировать приложение")
    async def fw_block_app(message: types.Message):
        user_id = message.from_user.id
        refresh_processes(user_id)
        kb_proc = build_process_keyboard(user_id, "fw_block_app_select")
        await message.answer("Выбери процесс для блокировки сети:", reply_markup=kb_proc)

    @dp.message_handler(lambda m: m.from_user.id in authorized_users and net_section.get(m.from_user.id) == "firewall" and m.text == "Разблокировать приложение")
    async def fw_unblock_app(message: types.Message):
        user_id = message.from_user.id
        refresh_processes(user_id)
        kb_proc = build_process_keyboard(user_id, "fw_unblock_app_select")
        await message.answer("Выбери процесс для снятия блокировки:", reply_markup=kb_proc)

    @dp.message_handler(lambda m: m.from_user.id in authorized_users and net_section.get(m.from_user.id) == "firewall" and m.text == "Блокировать порт")
    async def fw_block_port_from_firewall(message: types.Message):
        user_id = message.from_user.id
        net_section[user_id] = "ports"
        refresh_ports(user_id)
        kb_ports = build_port_keyboard(user_id, "fw_block_select")
        await message.answer("Выбери порт для блокировки в брандмауэре:", reply_markup=kb_ports)

    @dp.message_handler(lambda m: m.from_user.id in authorized_users and net_section.get(m.from_user.id) == "firewall" and m.text == "Разблокировать порт")
    async def fw_unblock_port_from_firewall(message: types.Message):
        user_id = message.from_user.id
        net_section[user_id] = "ports"
        refresh_ports(user_id)
        kb_ports = build_port_keyboard(user_id, "fw_unblock_select")
        await message.answer("Выбери порт для разблокировки:", reply_markup=kb_ports)

    @dp.message_handler(lambda m: m.from_user.id in authorized_users and net_section.get(m.from_user.id) == "firewall" and m.text == "Правила бота в брандмауэре")
    async def fw_rules_list(message: types.Message):
        report = firewall.list_bot_rules()
        await send_chunked(message, report, reply_markup=kb.get_firewall_keyboard())
    # --- Адаптеры ---
    @dp.message_handler(lambda m: m.from_user.id in authorized_users and net_mode.get(m.from_user.id) and m.text == "Сетевые адаптеры")
    async def adapters_menu(message: types.Message):
        user_id = message.from_user.id
        net_section[user_id] = "adapters"
        refresh_adapters(user_id)
        net_step.pop(user_id, None)
        await message.answer("Работа с сетевыми адаптерами.", reply_markup=kb.get_adapters_keyboard())

    @dp.message_handler(lambda m: m.from_user.id in authorized_users and net_section.get(m.from_user.id) == "adapters" and m.text == "Состояние адаптеров")
    async def adapters_status(message: types.Message):
        report = netinfo.adapter_statuses()
        await send_chunked(message, report, reply_markup=kb.get_adapters_keyboard())

    @dp.message_handler(lambda m: m.from_user.id in authorized_users and net_section.get(m.from_user.id) == "adapters" and m.text == "Обновить список адаптеров")
    async def adapters_refresh(message: types.Message):
        user_id = message.from_user.id
        refresh_adapters(user_id)
        await message.answer("Список адаптеров обновлён.", reply_markup=kb.get_adapters_keyboard())

    @dp.message_handler(lambda m: m.from_user.id in authorized_users and net_section.get(m.from_user.id) == "adapters" and m.text == "Отключить адаптер")
    async def adapter_disable(message: types.Message):
        user_id = message.from_user.id
        kb_adapt = build_adapter_keyboard(user_id, "adapter_disable_select")
        await message.answer("Выбери адаптер для отключения:", reply_markup=kb_adapt)

    @dp.message_handler(lambda m: m.from_user.id in authorized_users and net_section.get(m.from_user.id) == "adapters" and m.text == "Включить адаптер")
    async def adapter_enable(message: types.Message):
        user_id = message.from_user.id
        kb_adapt = build_adapter_keyboard(user_id, "adapter_enable_select")
        await message.answer("Выбери адаптер для включения:", reply_markup=kb_adapt)
    # --- Диагностика ---
    @dp.message_handler(lambda m: m.from_user.id in authorized_users and net_mode.get(m.from_user.id) and m.text == "Диагностика")
    async def diag_menu(message: types.Message):
        user_id = message.from_user.id
        net_section[user_id] = "diag"
        net_step.pop(user_id, None)
        await message.answer("Диагностика сети (готовые сценарии ping/traceroute).", reply_markup=kb.get_diag_keyboard())

    @dp.message_handler(lambda m: m.from_user.id in authorized_users and net_section.get(m.from_user.id) == "diag" and m.text.startswith("Ping "))
    async def diag_ping(message: types.Message):
        target = message.text.replace("Ping ", "", 1).strip()
        report = diagnostics.ping(target)
        await send_chunked(message, report, reply_markup=kb.get_diag_keyboard())

    @dp.message_handler(lambda m: m.from_user.id in authorized_users and net_section.get(m.from_user.id) == "diag" and m.text.startswith("Traceroute "))
    async def diag_trace(message: types.Message):
        target = message.text.replace("Traceroute ", "", 1).strip()
        report = diagnostics.traceroute(target)
        await send_chunked(message, report, reply_markup=kb.get_diag_keyboard())

    @dp.message_handler(lambda m: m.from_user.id in authorized_users and net_section.get(m.from_user.id) == "diag" and m.text == "Сброс DNS")
    async def diag_flushdns(message: types.Message):
        report = diagnostics.flush_dns()
        await send_chunked(message, report, reply_markup=kb.get_diag_keyboard())

    @dp.message_handler(lambda m: m.from_user.id in authorized_users and net_section.get(m.from_user.id) == "diag" and m.text == "Обновить IP (renew)")
    async def diag_renew(message: types.Message):
        report = diagnostics.renew_ip()
        await send_chunked(message, report, reply_markup=kb.get_diag_keyboard())

    @dp.message_handler(lambda m: m.from_user.id in authorized_users and net_section.get(m.from_user.id) == "diag" and m.text == "Сброс IP (release)")
    async def diag_release(message: types.Message):
        report = diagnostics.release_ip()
        await send_chunked(message, report, reply_markup=kb.get_diag_keyboard())

    @dp.message_handler(lambda m: m.from_user.id in authorized_users and net_section.get(m.from_user.id) == "diag" and m.text == "IPConfig /all")
    async def diag_ipconfig(message: types.Message):
        report = diagnostics.ipconfig_all()
        await send_chunked(message, report, reply_markup=kb.get_diag_keyboard())

    # --- Сканер ---
    @dp.message_handler(lambda m: m.from_user.id in authorized_users and net_mode.get(m.from_user.id) and m.text == "Сканер сети")
    async def scan_menu(message: types.Message):
        user_id = message.from_user.id
        net_section[user_id] = "scan"
        net_step.pop(user_id, None)
        await message.answer("Быстрые сценарии сканирования портов.", reply_markup=kb.get_scan_keyboard())

    @dp.message_handler(lambda m: m.from_user.id in authorized_users and net_section.get(m.from_user.id) == "scan" and m.text.startswith("Сканировать "))
    async def scan_targets(message: types.Message):
        target = message.text.replace("Сканировать ", "", 1).strip()
        report = netinfo.quick_scan("127.0.0.1" if target == "localhost" else target, POPULAR_PORTS)
        await send_chunked(message, report, reply_markup=kb.get_scan_keyboard())
    # --- Обработка выборов и навигации ---
    @dp.message_handler(lambda m: m.from_user.id in authorized_users and net_mode.get(m.from_user.id) and net_step.get(m.from_user.id))
    async def net_router(message: types.Message):
        user_id = message.from_user.id
        section = net_section.get(user_id)
        step = net_step.get(user_id)
        text = message.text or ""

        if text in {
            "Обновить список портов",
            "Обновить список процессов",
            "Обновить список адаптеров",
            "Назад в модуль сети",
            "Назад в утилиты",
            "Информация о портах",
        }:
            return

        if text == "Следующая страница портов":
            port_page[user_id] = port_page.get(user_id, 0) + 1
            kb_ports = build_port_keyboard(user_id, step or "")
            await message.answer("Страница портов переключена.", reply_markup=kb_ports)
            return
        if text == "Предыдущая страница портов":
            port_page[user_id] = max(0, port_page.get(user_id, 0) - 1)
            kb_ports = build_port_keyboard(user_id, step or "")
            await message.answer("Страница портов переключена.", reply_markup=kb_ports)
            return

        if text == "Следующая страница процессов":
            proc_page[user_id] = proc_page.get(user_id, 0) + 1
            kb_proc = build_process_keyboard(user_id, step or "")
            await message.answer("Страница процессов переключена.", reply_markup=kb_proc)
            return
        if text == "Предыдущая страница процессов":
            proc_page[user_id] = max(0, proc_page.get(user_id, 0) - 1)
            kb_proc = build_process_keyboard(user_id, step or "")
            await message.answer("Страница процессов переключена.", reply_markup=kb_proc)
            return

        if text == "Следующая страница адаптеров":
            adapter_page[user_id] = adapter_page.get(user_id, 0) + 1
            kb_adapt = build_adapter_keyboard(user_id, step or "")
            await message.answer("Страница адаптеров переключена.", reply_markup=kb_adapt)
            return
        if text == "Предыдущая страница адаптеров":
            adapter_page[user_id] = max(0, adapter_page.get(user_id, 0) - 1)
            kb_adapt = build_adapter_keyboard(user_id, step or "")
            await message.answer("Страница адаптеров переключена.", reply_markup=kb_adapt)
            return

        if not net_mode.get(user_id):
            return

        if section == "ports" and step in {"port_info_select", "port_kill_select", "fw_block_select", "fw_unblock_select"}:
            selected = parse_port_from_button(text)
            if not selected:
                await message.answer("Выбери порт кнопкой или обнови список.", reply_markup=build_port_keyboard(user_id, step))
                return
            net_context[user_id] = {"port": str(selected["port"]), "proto": selected["proto"]}
            port = selected["port"]
            proto = selected["proto"]

            if step == "port_info_select":
                report = netinfo.port_details(port)
                net_step.pop(user_id, None)
                await send_chunked(message, report, reply_markup=kb.get_ports_keyboard())
                return

            if step == "port_kill_select":
                kb_confirm = types.ReplyKeyboardMarkup(resize_keyboard=True)
                kb_confirm.add(f"Подтвердить завершение {port}/{proto}")
                kb_confirm.add("Отмена", "Назад в модуль сети")
                net_step[user_id] = "port_kill_confirm"
                await message.answer(f"Завершить процессы на порту {port}/{proto}?", reply_markup=kb_confirm)
                return

            if step == "fw_block_select":
                kb_confirm = types.ReplyKeyboardMarkup(resize_keyboard=True)
                kb_confirm.add(f"Подтвердить блокировку {port}/{proto}")
                kb_confirm.add("Отмена", "Назад в модуль сети")
                net_step[user_id] = "fw_block_confirm"
                await message.answer(f"Блокировать порт {port}/{proto} (in/out) в брандмауэре?", reply_markup=kb_confirm)
                return

            if step == "fw_unblock_select":
                kb_confirm = types.ReplyKeyboardMarkup(resize_keyboard=True)
                kb_confirm.add(f"Подтвердить разблокировку {port}/{proto}")
                kb_confirm.add("Отмена", "Назад в модуль сети")
                net_step[user_id] = "fw_unblock_confirm"
                await message.answer(f"Разблокировать порт {port}/{proto}?", reply_markup=kb_confirm)
                return

        if section == "ports" and step in {"port_kill_confirm", "fw_block_confirm", "fw_unblock_confirm"}:
            if text.startswith("Подтвердить завершение"):
                info = net_context.get(user_id, {})
                port = int(info.get("port", "0"))
                report = netinfo.kill_process_on_port(port)
                net_step.pop(user_id, None)
                await send_chunked(message, report, reply_markup=kb.get_ports_keyboard())
                return
            if text.startswith("Подтвердить блокировку"):
                info = net_context.get(user_id, {})
                port = int(info.get("port", "0"))
                proto = info.get("proto", "TCP")
                report = firewall.block_port(port, proto)
                net_step.pop(user_id, None)
                await send_chunked(message, report, reply_markup=kb.get_ports_keyboard())
                return
            if text.startswith("Подтвердить разблокировку"):
                info = net_context.get(user_id, {})
                port = int(info.get("port", "0"))
                proto = info.get("proto", "TCP")
                report = firewall.unblock_port(port, proto)
                net_step.pop(user_id, None)
                await send_chunked(message, report, reply_markup=kb.get_ports_keyboard())
                return
            if text == "Отмена":
                net_step.pop(user_id, None)
                await message.answer("Действие отменено.", reply_markup=kb.get_ports_keyboard())
                return

        if section == "firewall" and step in {"fw_block_app_select", "fw_unblock_app_select"}:
            if text == "Обновить список процессов":
                refresh_processes(user_id)
                kb_proc = build_process_keyboard(user_id, step)
                await message.answer("Список процессов обновлён.", reply_markup=kb_proc)
                return
            proc = parse_proc_from_button(user_id, text)
            if not proc:
                kb_proc = build_process_keyboard(user_id, step)
                await message.answer("Выбери процесс кнопкой.", reply_markup=kb_proc)
                return
            net_context[user_id] = proc
            name = proc.get("name")
            exe = proc.get("exe") or "<путь не найден>"
            kb_confirm = types.ReplyKeyboardMarkup(resize_keyboard=True)
            if step == "fw_block_app_select":
                kb_confirm.add("Подтвердить блокировку приложения")
                kb_confirm.add("Отмена", "Назад в модуль сети")
                net_step[user_id] = "fw_block_app_confirm"
                await message.answer(f"Блокировать сеть для {name} ({exe})?", reply_markup=kb_confirm)
            else:
                kb_confirm.add("Подтвердить разблокировку приложения")
                kb_confirm.add("Отмена", "Назад в модуль сети")
                net_step[user_id] = "fw_unblock_app_confirm"
                await message.answer(f"Снять блокировку сети для {name} ({exe})?", reply_markup=kb_confirm)
            return

        if section == "firewall" and step in {"fw_block_app_confirm", "fw_unblock_app_confirm"}:
            if text.startswith("Подтвердить блокировку приложения"):
                proc = net_context.get(user_id, {})
                exe = proc.get("exe") or ""
                report = firewall.block_app(exe) if exe else "Путь приложения не найден."
                net_step.pop(user_id, None)
                await send_chunked(message, report, reply_markup=kb.get_firewall_keyboard())
                return
            if text.startswith("Подтвердить разблокировку приложения"):
                proc = net_context.get(user_id, {})
                exe = proc.get("exe") or ""
                report = firewall.unblock_app(exe) if exe else "Путь приложения не найден."
                net_step.pop(user_id, None)
                await send_chunked(message, report, reply_markup=kb.get_firewall_keyboard())
                return
            if text == "Отмена":
                net_step.pop(user_id, None)
                await message.answer("Действие отменено.", reply_markup=kb.get_firewall_keyboard())
                return

        if section == "adapters" and step in {"adapter_disable_select", "adapter_enable_select"}:
            if text == "Обновить список адаптеров":
                refresh_adapters(user_id)
                kb_adapt = build_adapter_keyboard(user_id, step)
                await message.answer("Список адаптеров обновлён.", reply_markup=kb_adapt)
                return
            adapters = adapter_cache.get(user_id, [])
            if text not in adapters:
                kb_adapt = build_adapter_keyboard(user_id, step)
                await message.answer("Выбери адаптер кнопкой.", reply_markup=kb_adapt)
                return
            net_context[user_id] = {"adapter": text}
            kb_confirm = types.ReplyKeyboardMarkup(resize_keyboard=True)
            if step == "adapter_disable_select":
                kb_confirm.add("Подтвердить отключение адаптера")
                kb_confirm.add("Отмена", "Назад в модуль сети")
                net_step[user_id] = "adapter_disable_confirm"
                await message.answer(f"Отключить адаптер «{text}»?", reply_markup=kb_confirm)
            else:
                kb_confirm.add("Подтвердить включение адаптера")
                kb_confirm.add("Отмена", "Назад в модуль сети")
                net_step[user_id] = "adapter_enable_confirm"
                await message.answer(f"Включить адаптер «{text}»?", reply_markup=kb_confirm)
            return

        if section == "adapters" and step in {"adapter_disable_confirm", "adapter_enable_confirm"}:
            adapter = net_context.get(user_id, {}).get("adapter")
            if text.startswith("Подтвердить отключение") and adapter:
                report = netinfo.disable_adapter(adapter)
                net_step.pop(user_id, None)
                await send_chunked(message, report, reply_markup=kb.get_adapters_keyboard())
                return
            if text.startswith("Подтвердить включение") and adapter:
                report = netinfo.enable_adapter(adapter)
                net_step.pop(user_id, None)
                await send_chunked(message, report, reply_markup=kb.get_adapters_keyboard())
                return
            if text == "Отмена":
                net_step.pop(user_id, None)
                await message.answer("Действие отменено.", reply_markup=kb.get_adapters_keyboard())
                return

    # --- Fallback ---
    @dp.message_handler(lambda m: m.from_user.id in authorized_users and net_mode.get(m.from_user.id))
    async def net_fallback(message: types.Message):
        keyboard = current_keyboard(message.from_user.id)
        await message.answer("Используй кнопки меню для действий или вернись в утилиты.", reply_markup=keyboard)
