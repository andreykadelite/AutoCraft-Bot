"""Модуль статуса сервера и сети для Telegram‑бота.

Подключение:
    import modul_status_info
    modul_status_info.register_handlers(dp)

Модуль НЕ трогает меню утилит, просто добавляет команды:
    /status_server  - статус сервера
    /status_network - статус сети
    Статус сервера  - по тексту сообщения
    Статус сети     - по тексту сообщения
"""

import logging
import platform
import socket
import subprocess

import psutil
from wmi import WMI
import speedtest

from aiogram import types  # для типов сообщений

# Попробуем подключить реестр главного меню.
# Если его нет, модуль продолжит работать как раньше.
try:
    from mainmenu_registry import register_main_item
except ImportError:
    register_main_item = None


def _register_mainmenu_items():
    """
    Регистрирует кнопки главного меню для этого модуля.

    - «Статус сервера»
    - «Статус сети»

    Если mainmenu_registry отсутствует, тихо ничего не делает.
    """
    if register_main_item is None:
        return
    try:
        register_main_item(
            key="status_server",
            title="Статус сервера",
            trigger_text="Статус сервера",
            group="main",
            order=10,
            description="Показ статуса сервера (CPU, RAM, диски, ОС)"
        )
        register_main_item(
            key="status_network",
            title="Статус сети",
            trigger_text="Статус сети",
            group="main",
            order=20,
            description="Показ сетевого статуса, IP и скорости"
        )
    except Exception as e:
        logging.getLogger(__name__).warning(
            "Не удалось зарегистрировать пункты главного меню для status_info: %s", e
        )


# Автоматическая регистрация пунктов главного меню при импорте модуля
try:
    _register_mainmenu_items()
except Exception:
    pass



# Локальные обёртки для логов.
# Если в основном скрипте уже настроены логгеры "БОТ" и "КОМ",
# то сообщения попадут в те же файлы.
_bot_logger = logging.getLogger("БОТ")
_com_logger = logging.getLogger("КОМ")

def write_bot_log(entry: str):
    try:
        _bot_logger.info(entry)
    except Exception:
        logging.getLogger(__name__).info(entry)

def write_com_log(entry: str):
    try:
        _com_logger.info(entry)
    except Exception:
        logging.getLogger(__name__).info(entry)


def get_os_status():
        """
        Возвращает точную информацию об ОС для Windows через WMI и для других ОС через platform.uname().
        """
        try:
            if platform.system() == "Windows":
                w = WMI()
                os_info = w.Win32_OperatingSystem()[0]
                name = os_info.Caption.strip()
                version = os_info.Version
                arch = os_info.OSArchitecture
                return f"ОС: {name} (Версия {version}, {arch})"
            else:
                uname = platform.uname()
                return f"ОС: {uname.system} {uname.release} ({uname.version}), {uname.machine}"
        except Exception as e:
            write_bot_log(f"[ОШИБКА] get_os_status: {e}")
            # fallback to original implementation
            return f"ОС: {platform.system()} {platform.release()} ({platform.version()})"

    
def get_cpu_status():
        """
        Возвращает точную информацию о процессоре: модель, загрузку, ядра и частоту (через WMI на Windows).
        """
        try:
            if platform.system() == "Windows":
                w = WMI()
                cpu_w = w.Win32_Processor()[0]
                name = cpu_w.Name.strip()
                cores = cpu_w.NumberOfCores
                threads = cpu_w.NumberOfLogicalProcessors
                usage = psutil.cpu_percent(interval=1)
                freq = cpu_w.MaxClockSpeed  # MaxClockSpeed в МГц
                return (
                    f"CPU: {name}\n"
                    f"Загрузка: {usage}%\n"
                    f"Ядер: {cores} физ., {threads} лог.\n"
                    f"Частота: {freq} МГц"
                )
            else:
                cpu_usage = psutil.cpu_percent(interval=1)
                physical_cores = psutil.cpu_count(logical=False)
                total_cores = psutil.cpu_count(logical=True)
                cpu_freq = psutil.cpu_freq()
                if cpu_freq:
                    current_freq = f"{cpu_freq.current:.2f}"
                else:
                    current_freq = "Недоступно"
                return (
                    f"CPU: {platform.processor()}\n"
                    f"Загрузка: {cpu_usage}%\n"
                    f"Ядер: {physical_cores} физ., {total_cores} лог.\n"
                    f"Частота: {current_freq} МГц"
                )
        except Exception as e:
            write_bot_log(f"[ОШИБКА] get_cpu_status: {e}")
            # fallback to original implementation
            cpu_usage = psutil.cpu_percent(interval=1)
            physical_cores = psutil.cpu_count(logical=False)
            total_cores = psutil.cpu_count(logical=True)
            cpu_freq = psutil.cpu_freq()
            if cpu_freq:
                current_freq = f"{cpu_freq.current:.2f}"
            else:
                current_freq = "Недоступно"
            return (
                f"CPU: {platform.processor()}\n"
                f"Загрузка: {cpu_usage}%\n"
                f"Ядер: {physical_cores} физ., {total_cores} лог.\n"
                f"Частота: {current_freq} МГц"
            )

    
def get_ram_status():
        ram = psutil.virtual_memory()
        return (
            f"RAM: {round(ram.total/(1024**3), 2)} ГБ общий, "
            f"{round(ram.used/(1024**3), 2)} ГБ использовано, "
            f"{round(ram.available/(1024**3), 2)} ГБ доступно\n"
            f"Загрузка: {ram.percent}%"
        )

    
def get_disk_status():
        partitions = psutil.disk_partitions()
        result = []
        for partition in partitions:
            try:
                usage = psutil.disk_usage(partition.mountpoint)
                result.append(
                    f"Диск {partition.device} ({partition.fstype}, {partition.mountpoint}):\n"
                    f"  {round(usage.total/(1024**3),2)} ГБ всего, "
                    f"{round(usage.used/(1024**3),2)} ГБ использовано ({usage.percent}%), "
                    f"{round(usage.free/(1024**3),2)} ГБ свободно"
                )
            except Exception:
                result.append(f"Диск {partition.device}: недоступно")
        return "\n".join(result)

    
def get_network_status():
        hostname = socket.gethostname()
        net_if_stats = psutil.net_if_stats()
        net_if_addrs = psutil.net_if_addrs()
        connected_interface_details = []
        internal_ip = None
        for iface, stats in net_if_stats.items():
            if stats.isup:
                addrs = net_if_addrs.get(iface, [])
                ipv4_found = False
                info_list = []
                for addr in addrs:
                    if addr.family == socket.AF_INET:
                        if not addr.address.startswith("127."):
                            ipv4_found = True
                            info_list.append(f"IPv4: {addr.address}")
                    elif addr.family == socket.AF_INET6:
                        info_list.append(f"IPv6: {addr.address}")
                    elif hasattr(socket, 'AF_PACKET') and addr.family == socket.AF_PACKET:
                        info_list.append(f"MAC: {addr.address}")
                if ipv4_found:
                    connected_interface_details.append(f"{iface}: " + ", ".join(info_list))
                    if internal_ip is None:
                        for addr in addrs:
                            if addr.family == socket.AF_INET and not addr.address.startswith("127."):
                                internal_ip = addr.address
                                break
        if not internal_ip:
            try:
                internal_ip = socket.gethostbyname(hostname)
            except Exception:
                internal_ip = "Не удалось получить"
        try:
            external_ip = subprocess.check_output("curl -s ifconfig.me", shell=True).decode("utf-8").strip()
        except Exception:
            external_ip = "Не удалось получить"
        return hostname, internal_ip, external_ip, connected_interface_details

    
def test_speed():
        try:
            st = speedtest.Speedtest()
            st.get_best_server()
            download = st.download() / 1_000_000
            upload = st.upload() / 1_000_000
            return f"Скорость: загрузка {round(download,2)} Мбит/с, отправка {round(upload,2)} Мбит/с"
        except Exception as e:
            return f"Ошибка теста скорости: {str(e)}"

    

def register_handlers(dp):
    """Регистрация команд статуса сервера и сети.

    Ничего не добавляет в меню утилит, работает только по командам / тексту.
    """

    @dp.message_handler(commands=['status_server'])
    async def cmd_status_server(message: types.Message):
        write_com_log(f"Пользователь {message.from_user.id} запросил статус сервера (через модуль).")
        await message.answer(get_os_status())
        await message.answer(get_cpu_status())
        await message.answer(get_ram_status())
        await message.answer(get_disk_status())

    @dp.message_handler(commands=['status_network'])
    async def cmd_status_network(message: types.Message):
        write_com_log(f"Пользователь {message.from_user.id} запросил статус сети (через модуль).")
        hostname, internal_ip, external_ip, interface_details = get_network_status()
        if interface_details:
            for detail in interface_details:
                await message.answer("Интерфейс:\n" + detail)
        else:
            await message.answer("Нет подключённых интерфейсов")
        await message.answer(f"Имя хоста: {hostname}")
        await message.answer(f"Внутренний IP: {internal_ip}")
        await message.answer(f"Внешний IP: {external_ip}")
        await message.answer("Измерение скорости, подождите...")
        await message.answer(test_speed())

    # Дополнительно поддерживаем старые текстовые триггеры,
    # чтобы можно было просто написать «Статус сервера» или «Статус сети».
    @dp.message_handler(lambda message: (message.text or '').strip().lower() == 'статус сервера')
    async def text_status_server(message: types.Message):
        await cmd_status_server(message)

    @dp.message_handler(lambda message: (message.text or '').strip().lower() == 'статус сети')
    async def text_status_network(message: types.Message):
        await cmd_status_network(message)
